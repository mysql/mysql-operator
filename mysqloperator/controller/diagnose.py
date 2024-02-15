# Copyright (c) 2020, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from re import L
from kubernetes.client.rest import ApiException
from .innodbcluster.cluster_api import InnoDBCluster, MySQLPod
import typing
from typing import Optional, TYPE_CHECKING, Tuple, List, Set, Dict, cast
from . import shellutils, consts, errors
import kopf
import mysqlsh
import enum
import time
if TYPE_CHECKING:
    from mysqlsh.mysql import ClassicSession
    from mysqlsh import Dba, Cluster

mysql = mysqlsh.mysql

#
# InnoDB Cluster Instance Diagnostic Statuses
#


class InstanceDiagStatus(enum.Enum):
    # GR ONLINE
    ONLINE = "ONLINE"

    # GR RECOVERING
    RECOVERING = "RECOVERING"

    # GR ERROR
    ERROR = "ERROR"

    # GR OFFLINE or any indication that makes it's certain that GR is not ONLINE or RECOVERING
    OFFLINE = "OFFLINE"

    # Instance is not a member (and never was)
    # in addition to being OFFLINE
    NOT_MANAGED = "NOT_MANAGED"

    # Instance of an unmanaged replication group. Probably was already member but got removed
    UNMANAGED = "UNMANAGED"

    # Instance is not reachable, maybe networking issue
    UNREACHABLE = "UNREACHABLE"

    # Uncertain because we can't connect or query it
    UNKNOWN = "UNKNOWN"


class InstanceStatus:
    pod: Optional[MySQLPod] = None
    status: InstanceDiagStatus = InstanceDiagStatus.UNKNOWN

    connect_error: Optional[int] = None

    view_id: Optional[str] = None
    is_primary: Optional[bool] = None
    in_quorum: Optional[bool] = None
    peers: Optional[dict] = None
    gtid_executed: Optional[str] = None

    def __repr__(self) -> str:
        return f"InstanceStatus: pod={self.pod} status={self.status} connect_error={self.connect_error} view_id={self.view_id} is_primary={self.is_primary} in_quorum={self.in_quorum} peers={self.peers}"

def diagnose_instance(pod: MySQLPod, logger, dba: 'Dba' = None) -> InstanceStatus:
    """
    Check state of an instance in the given pod.

    State is checked in isolation. That is, using its own view of the group
    and its own local copy of the metadata (if there is one). Thus, it can be
    incorrect, if for example, the pod was deleted and didn't have its copy of
    the metadata updated or if there's a split-brain.
    """
    status = InstanceStatus()
    status.pod = pod

    if not dba:
        try:
            dba = mysqlsh.connect_dba(pod.endpoint_co)
        except mysqlsh.Error as e:
            logger.info(f"Could not connect to {pod.endpoint}: error={e}")
            status.connect_error = e.code

            if mysql.ErrorCode.CR_MAX_ERROR >= e.code >= mysql.ErrorCode.CR_MIN_ERROR:
                # client side errors mean we can't connect to the server, but the
                # problem could be in the client or network and not the server

                # Check status of the pod
                pod.reload()
                logger.debug(f"{pod.endpoint}: pod.phase={pod.phase}  deleting={pod.deleting}")
                if pod.phase != "Running" or not pod.check_containers_ready() or pod.deleting:
                    # not ONLINE for sure if the Pod is not running
                    status.status = InstanceDiagStatus.OFFLINE
            else:
                if shellutils.check_fatal_connect(e, pod.endpoint_url_safe, logger):
                    raise

            return status

    cluster = None
    if dba:
        status.gtid_executed = dba.session.run_sql("select @@gtid_executed").fetch_one()[0]

        try:
            # TODO: we want to check from individual Pod's/Server's perspective
            #       it will now check based from primary N times
            cluster = dba.get_cluster()
        except mysqlsh.Error as e:
            logger.info(f"get_cluster() error for {pod.endpoint}: error={e}")

            # TODO check for invalid metadata errors
            # Note: get_cluster() on a member that was previously removed
            # can fail as OFFLINE instead of NOT_MANAGED if its copy of the
            # metadata lacks the trx where it was removed
            if e.code == errors.SHERR_DBA_BADARG_INSTANCE_NOT_ONLINE:
                status.status = InstanceDiagStatus.OFFLINE
            elif e.code == errors.SHERR_DBA_BADARG_INSTANCE_NOT_MANAGED:
                status.status = InstanceDiagStatus.NOT_MANAGED
            else:
                if shellutils.check_fatal(
                        e, pod.endpoint_url_safe, "get_cluster()", logger):
                    raise
                status.status = InstanceDiagStatus.UNKNOWN
        except RuntimeError as e:
            e_str = str(e)
            if e_str.find("unmanaged replication group"):
                status.status = InstanceDiagStatus.UNMANAGED
            else:
                logger.info(f"diagnose_instance: 2 Runtime Error [{e}]")
                status.status = InstanceDiagStatus.UNKNOWN

    if cluster:
        try:
            mstatus = cluster.status({"extended": 1})

            cluster_status = mstatus["defaultReplicaSet"]["status"]
            status.view_id = mstatus["defaultReplicaSet"]["groupViewId"]

            if cluster_status.startswith("OK"):
                status.in_quorum = True
            else:
                logger.info(
                    f"""No quorum visible from {pod.endpoint}: status={cluster_status}  topology={";".join([f'{m},{i["status"]}' for m, i in mstatus["defaultReplicaSet"]["topology"].items()])}""")
                status.in_quorum = False

            members = {}
            mystate = None
            for member, info in mstatus["defaultReplicaSet"]["topology"].items():
                if pod.instance_type == "group-member":
                    state = info["status"]
                    members[member] = state
                    if member == pod.endpoint:
                        mystate = state
                        if state == "ONLINE":
                            status.is_primary = info["memberRole"] == "PRIMARY"
                elif pod.instance_type == "read-replica":
                    if "readReplicas" in info:
                        for rr_member, rr_info in info["readReplicas"].items():
                            if rr_member == pod.endpoint:
                                mystate = rr_info["status"]
                else:
                    raise Exception(f"Unknown instance type for {pod.name}: {pod.instance_type}")

            if not mystate:
                # TODO
                raise Exception(
                    f"Could not find {pod} in local cluster.status() output")

            status.peers = members

            if mystate == "ONLINE":
                status.status = InstanceDiagStatus.ONLINE
            elif mystate == "RECOVERING":
                status.status = InstanceDiagStatus.RECOVERING
            elif mystate == "ERROR":
                status.status = InstanceDiagStatus.ERROR
            elif mystate == "OFFLINE":
                status.status = InstanceDiagStatus.OFFLINE
            elif mystate == "UNREACHABLE":
                status.status = InstanceDiagStatus.UNREACHABLE
            else:
                assert False, f"{pod.endpoint}: bad state {mystate}"
        except mysqlsh.Error as e:
            if shellutils.check_fatal(
                    e, pod.endpoint_url_safe, "status()", logger):
                raise

            logger.info(f"status() failed at {pod.endpoint}: error={e}")
            status.status = InstanceDiagStatus.UNKNOWN

    return status


#
# InnoDB Cluster Candidate Instance Statuses
#

class CandidateDiagStatus(enum.Enum):
    UNKNOWN = None

    # Instance is already a member of the cluster
    MEMBER = "MEMBER"

    # Instance is a member of the cluster but can rejoin it
    REJOINABLE = "REJOINABLE"

    # Instance is not yet a member of the cluster but can join it
    JOINABLE = "JOINABLE"

    # Instance is a member of the cluster but has a problem that prevents it
    # from rejoining
    BROKEN = "BROKEN"

    # Instance is not yet a member of the cluster and can't join it
    UNSUITABLE = "UNSUITABLE"

    # Instance can't be reached
    UNREACHABLE = "UNREACHABLE"


class CandidateStatus:
    status: CandidateDiagStatus = CandidateDiagStatus.UNKNOWN

    # Reasons for broken/unsuitable
    bad_gtid_set: Optional[str] = None


def check_errant_gtids(primary_session: 'ClassicSession', pod: MySQLPod, pod_dba: 'Dba', logger) -> Optional[str]:
    try:
        gtid_set = pod_dba.session.run_sql(
            "SELECT @@globals.GTID_EXECUTED").fetch_one()[0]
    except mysqlsh.Error as e:
        if e.code == mysql.ErrorCode.ER_UNKNOWN_SYSTEM_VARIABLE:
            return None
        else:
            raise

    if gtid_set:
        # find primary
        errants = primary_session.run_sql(
            "SELECT GTID_SUBTRACT(?, @@globals.GTID_EXECUTED)", [gtid_set]).fetch_one()[0]
        return errants
    return None


def diagnose_cluster_candidate(primary_session: 'ClassicSession', cluster: 'Cluster', pod: MySQLPod, pod_dba: 'Dba', logger) -> CandidateStatus:
    """
    Check status of an instance that's about to be added to the cluster or
    rejoin it, relative to the given cluster. Also checks whether the instance
    can join it.
    """

    status = CandidateStatus()

    istatus = diagnose_instance(pod, logger, pod_dba)

    if istatus.status == InstanceDiagStatus.UNKNOWN:
        status.status = CandidateDiagStatus.UNREACHABLE
    elif istatus.status in (InstanceDiagStatus.ONLINE, InstanceDiagStatus.RECOVERING):
        status.status = CandidateDiagStatus.MEMBER
        logger.debug(f"{pod} is {istatus.status} -> {status.status}")
    elif istatus.status in (InstanceDiagStatus.NOT_MANAGED, InstanceDiagStatus.UNMANAGED):
        status.bad_gtid_set = check_errant_gtids(
            primary_session, pod, pod_dba, logger)
        if status.bad_gtid_set:
            logger.warning(
                f"{pod} has errant transactions relative to the cluster: errant_gtids={status.bad_gtid_set}")

        if not status.bad_gtid_set:
            status.status = CandidateDiagStatus.JOINABLE
        else:
            status.status = CandidateDiagStatus.UNSUITABLE

        logger.debug(
            f"{pod} is {istatus.status}, errant_gtids={status.bad_gtid_set} -> {status.status}")
    elif istatus.status in (InstanceDiagStatus.OFFLINE, InstanceDiagStatus.ERROR):
        if istatus.status == InstanceDiagStatus.ERROR:
            # check for fatal GR errors
            fatal_error = None
        else:
            fatal_error = None

        status.bad_gtid_set = check_errant_gtids(
            primary_session, pod, pod_dba, logger)
        if status.bad_gtid_set:
            logger.warning(
                f"{pod} has errant transactions relative to the cluster: errant_gtids={status.bad_gtid_set}")
        # TODO disable queryMembers
        if pod.endpoint in cluster.status()["defaultReplicaSet"]["topology"].keys():
            # already a member of the cluster
            if not status.bad_gtid_set and not fatal_error:
                status.status = CandidateDiagStatus.REJOINABLE
            else:
                status.status = CandidateDiagStatus.BROKEN
        else:
            if not status.bad_gtid_set and not fatal_error:
                status.status = CandidateDiagStatus.JOINABLE
            else:
                status.status = CandidateDiagStatus.UNSUITABLE

        logger.debug(
            f"{pod} is {istatus.status}  errant_gtids={status.bad_gtid_set}  fatal_error={fatal_error} -> {status.status}")
    else:
        raise Exception(
            f"Unexpected pod state pod={pod} status={istatus.status}")

    return status

#
# InnoDB Cluster Diagnostic Statuses
#


class ClusterDiagStatus(enum.Enum):
    ONLINE = "ONLINE"
    # - All members are reachable or part of the quorum
    # - Reachable members form a quorum between themselves
    # - There are no unreachable members that are not in the quorum
    # - All members are ONLINE

    ONLINE_PARTIAL = "ONLINE_PARTIAL"
    # - All members are reachable or part of the quorum
    # - Some reachable members form a quorum between themselves
    # - There may be members outside of the quorum in any state, but they must not form a quorum
    # Note that there may be members that think are ONLINE, but minority in a view with UNREACHABLE members

    OFFLINE = "OFFLINE"
    # - All members are reachable
    # - All cluster members are OFFLINE/ERROR (or being deleted)
    # - GTID set of all members are consistent
    # We're sure that the cluster is completely down with no quorum hiding somewhere
    # The cluster can be safely rebooted

    NO_QUORUM = "NO_QUORUM"
    # - All members are reachable
    # - All cluster members are either OFFLINE/ERROR or ONLINE but with no quorum
    # A split-brain with no-quorum still falls in this category
    # The cluster can be safely restored

    SPLIT_BRAIN = "SPLIT_BRAIN"
    # - Some but not all members are unreachable
    # - There are multiple ONLINE/RECOVERING members forming a quorum, but with >1
    # different views
    # If some members are not reachable, they could either be forming more errant
    # groups or be unavailable, but that doesn't make much dfifference.

    ONLINE_UNCERTAIN = "ONLINE_UNCERTAIN"
    # - Some members are unreachable
    # - Reachable members form a quorum between themselves
    # - There are unreachable members that are not in the quorum and have unknown state
    # Because there are members with unknown state, the possibility that there's a
    # split-brain exists.

    OFFLINE_UNCERTAIN = "OFFLINE_UNCERTAIN"
    # OFFLINE with unreachable members

    NO_QUORUM_UNCERTAIN = "NO_QUORUM_UNCERTAIN"
    # NO_QUORUM with unreachable members

    SPLIT_BRAIN_UNCERTAIN = "SPLIT_BRAIN_UNCERTAIN"
    # SPLIT_BRAIN with unreachable members

    UNKNOWN = "UNKNOWN"
    # - No reachable/connectable members
    # We have no idea about the state of the cluster, so nothing can be done about it
    # (even if we wanted)

    INITIALIZING = "INITIALIZING"
    # - Cluster is not marked as initialized in Kubernetes
    # The cluster hasn't been created/initialized yet, so we can safely create it

    FINALIZING = "FINALIZING"
    # - Cluster object is marked as being deleted

    INVALID = "INVALID"
    # - A (currently) undiagnosable and unrecoverable mess that doesn't fit any other state

    PENDING = "PENDING"


def find_group_partitions(online_pod_info: Dict[str, InstanceStatus],
                          pods: Set[MySQLPod], logger) -> Tuple[List[List[InstanceStatus]], List[Set[MySQLPod]]]:
    # List of group partitions that have quorum and can execute transactions.
    # If there's more than 1, then there's a split-brain. If there's none, then
    # we have no availability.
    active_partitions: List[List[InstanceStatus]] = []
    # List of group partitions that have no quorum and can't execute transactions.
    blocked_partitions: List[Set[MySQLPod]] = []

    all_pods = {}
    for pod in pods:
        all_pods[pod.endpoint] = pod

    no_primary_active_partitions = []

    for ep, p in online_pod_info.items():
        # logger.info(f"{ep}:  {'QUORUM' if p.in_quorum else 'NOQUORUM'} {'PRIM' if p.is_primary else 'SEC'} ONLINE_PODS={online_pod_info.keys()}")
        # logger.info(f"PEERS OF {ep}={p.peers}")
        if p.in_quorum:
            online_peers = [peer for peer, state in p.peers.items() if state in ("ONLINE", "RECOVERING")] # A: UNMANAGED ?
            missing = set(online_peers) - set(online_pod_info.keys())
            if missing:
                logger.info(
                    f"Group view of {ep} has {p.peers.keys()} but these are not ONLINE: {missing}")
                raise kopf.TemporaryError(
                    "Cluster status results inconsistent", delay=5)

            part = [online_pod_info[peer] for peer,
                    state in p.peers.items() if state in ("ONLINE", "RECOVERING")] # A: NOT_MANAGED ?
            if p.is_primary:
                active_partitions.append(part)
            else:
                no_primary_active_partitions.append(part)

    if not active_partitions and no_primary_active_partitions:
        # it's possible for a group with quorum to not have a PRIMARY
        # for a short time if the PRIMARY is removed from the group
        raise kopf.TemporaryError(
            "Cluster has quorum but no PRIMARY", delay=10)

    def active_partition_with(pod):
        for part in active_partitions:
            if pod.endpoint in part:
                return part
        return None

    # print()
    for ep, p in online_pod_info.items():
       #     print(ep, p.status, p.in_quorum, p.peers)
        if not p.in_quorum:
            part = active_partition_with(p)
            assert not part, f"Inconsistent group view, {p} not expected to be in {part}"

            part = set([all_pods[peer] for peer, state in p.peers.items()
                        if state not in ("(MISSING)", "UNREACHABLE")])
            if part not in blocked_partitions:
                blocked_partitions.append(part)
    # print("ACTIVE PARTS", active_partitions)
    # print("BLOCKED PARTS", blocked_partitions)
    # print()
    # sort by partition size
    blocked_partitions.sort(key=lambda x: len(x), reverse=True)

    return active_partitions, blocked_partitions


class ClusterStatus:
    status: ClusterDiagStatus = ClusterDiagStatus.UNKNOWN
    primary: Optional[MySQLPod] = None
    online_members: List[MySQLPod] = []
    quorum_candidates: Optional[list] = None
    gtid_executed: Dict[int,str] = {}


def do_diagnose_cluster(cluster: InnoDBCluster, logger) -> ClusterStatus:
    if not cluster.deleting:
        cluster.reload()

    all_pods = set(cluster.get_pods())

    last_known_quorum = cluster.get_last_known_quorum()

    # TODO last known quorum tracking
    log_msg = f"Diagnosing cluster {cluster.name}  deleting={cluster.deleting}  last_known_quorum={last_known_quorum}..."

    # Check if the cluster has already been initialized
    create_time = cluster.get_create_time()
    log_msg += f"create_time={create_time}  deleting={cluster.deleting}"

    if not create_time and not cluster.deleting:
        cluster_status = ClusterStatus()
        cluster_status.status = ClusterDiagStatus.INITIALIZING
        log_msg += f"\nCluster {cluster.name}  status={cluster_status.status}"
        return cluster_status

    all_member_pods = set()
    online_pods = set()
    offline_pods = set()
    unsure_pods = set()
    gtid_executed = {}

    online_pod_statuses = {}
    for pod in all_pods:
        # Diagnose the instance even if deleting - so we can remove it from the cluster and later re-add it
#        if pod.deleting:
#            logger.info(f"instance {pod} is deleting")
#            continue
        status = diagnose_instance(pod, logger)
        log_msg += f"\ndiag instance {pod} --> {status.status} quorum={status.in_quorum} gtid_executed={status.gtid_executed}"

        gtid_executed[pod.index] = status.gtid_executed

        if status.status == InstanceDiagStatus.UNKNOWN:
            unsure_pods.add(pod)
            all_member_pods.add(pod)
        elif status.status in (InstanceDiagStatus.OFFLINE, InstanceDiagStatus.ERROR, InstanceDiagStatus.UNMANAGED):
            offline_pods.add(pod)
            all_member_pods.add(pod)
        elif status.status in (InstanceDiagStatus.ONLINE, InstanceDiagStatus.RECOVERING):
            online_pod_statuses[pod.endpoint] = status
            online_pods.add(pod)
            all_member_pods.add(pod)
        elif status.status == InstanceDiagStatus.NOT_MANAGED:
            pass
        else:
            all_member_pods.add(pod)
            logger.error(f"Internal error processing pod {pod}")
            assert False

    log_msg += f"\n{cluster.name}: all={all_pods}  members={all_member_pods}  online={online_pods}  offline={offline_pods}  unsure={unsure_pods}"

    assert online_pods.union(offline_pods, unsure_pods) == all_member_pods

    cluster_status = ClusterStatus()

    cluster_status.gtid_executed = gtid_executed

    if online_pods:
        active_partitions, blocked_partitions = find_group_partitions(
            online_pod_statuses, all_member_pods, logger)
        log_msg += f"\nactive_partitions={active_partitions}  blocked_partitions={blocked_partitions}"

        if not active_partitions:
            # no quorum
            if unsure_pods:
                cluster_status.status = ClusterDiagStatus.NO_QUORUM_UNCERTAIN
            else:
                cluster_status.status = ClusterDiagStatus.NO_QUORUM
            if blocked_partitions:
                cluster_status.quorum_candidates = list(blocked_partitions[0])
        elif len(active_partitions) == 1:
            # ok
            if unsure_pods:
                cluster_status.status = ClusterDiagStatus.ONLINE_UNCERTAIN
            elif offline_pods:
                cluster_status.status = ClusterDiagStatus.ONLINE_PARTIAL
            else:
                cluster_status.status = ClusterDiagStatus.ONLINE
            cluster_status.online_members = [
                p.pod for p in active_partitions[0] if p.pod]
            for p in active_partitions[0]:
                if p.is_primary:
                    cluster_status.primary = p.pod
                    break
        else:
            # split-brain
            if unsure_pods:
                cluster_status.status = ClusterDiagStatus.SPLIT_BRAIN_UNCERTAIN
            else:
                cluster_status.status = ClusterDiagStatus.SPLIT_BRAIN
            cluster_status.online_members = []
            for part in active_partitions:
                cluster_status.online_members += [p.pod for p in part if p.pod]
    else:
        if cluster.deleting:
            cluster_status.status = ClusterDiagStatus.FINALIZING
        else:
            if offline_pods:
                if unsure_pods:
                    cluster_status.status = ClusterDiagStatus.OFFLINE_UNCERTAIN
                else:
                    cluster_status.status = ClusterDiagStatus.OFFLINE
            else:
                cluster_status.status = ClusterDiagStatus.UNKNOWN

    if cluster_status.status in (ClusterDiagStatus.UNKNOWN,
                                 ClusterDiagStatus.OFFLINE,
                                 ClusterDiagStatus.OFFLINE_UNCERTAIN,
                                 ClusterDiagStatus.SPLIT_BRAIN,
                                 ClusterDiagStatus.SPLIT_BRAIN_UNCERTAIN,
                                 ClusterDiagStatus.ONLINE_UNCERTAIN,
                                 ClusterDiagStatus.NO_QUORUM,
                                 ClusterDiagStatus.NO_QUORUM_UNCERTAIN):
        print(log_msg)

    logger.debug(f"Cluster {cluster.name}  status={cluster_status.status}")

    return cluster_status


def diagnose_cluster(cluster: InnoDBCluster, logger) -> ClusterStatus:
    """
    Diagnose the state of an InnoDB cluster, assuming it was already initialized.

    @param cluster - InnoDBCluster object to diagnose
    @param logger

    Returns (primary_pod, state)

    Notes:
    - we can only give a certain diagnostic if we can actually connect to and
    query from all pods
    - network related connectivity errors are not the same as server side errors,
    because server side errors (auth error, too many connections etc) mean we were
    able to at least connect to MySQL, so that means the server is up, but we don't
    know the GR status
    - we can consider a cluster to be ONLINE if there's a reachable majority, even
    if there are unreachable members. But the unreachable members could be forming
    their own split-brained cluster. Thus we separate between ONLINE (no possibility
    of split-brain) and ONLINE_PARTIAL
    - NO_QUORUM means there's no quorum anywhere at all, with certainty. If we can
    connect to only some members and none of them have quorum, then we can't report
    NO_QUORUM because the unreachable members could have a quorum of their own
    - If a member is part of the quorum but is not connectable, the state reported
    by the cluster takes precedence.
    - Exceptions that indicate there's something wrong with the deployment are
    bubbled up. For example:
        - auth error on a pod that's already initialized
    """

    return cast(ClusterStatus, shellutils.RetryLoop(logger).call(do_diagnose_cluster, cluster, logger))
