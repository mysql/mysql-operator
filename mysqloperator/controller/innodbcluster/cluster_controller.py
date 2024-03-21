# Copyright (c) 2020, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


from kopf._cogs.structs.bodies import Body
from .. import consts, errors, kubeutils, shellutils, utils, config, mysqlutils
from .. import diagnose
from ..backup import backup_objects
from ..shellutils import DbaWrap
from . import cluster_objects, router_objects
from .cluster_api import MySQLPod, InnoDBCluster, client
import typing
from typing import Optional, TYPE_CHECKING, Dict
from logging import Logger
if TYPE_CHECKING:
    from mysqlsh.mysql import ClassicSession
    from mysqlsh import Dba, Cluster
import os
import copy
import mysqlsh
import kopf
import datetime
import time

common_gr_options = {
    # Abort the server if member is kicked out of the group, which would trigger
    # an event from the container restart, which we can catch and act upon.
    # This also makes autoRejoinTries irrelevant.
    "exitStateAction": "ABORT_SERVER"
}

def select_pod_with_most_gtids(gtids: Dict[int, str]) -> int:
    pod_indexes = list(gtids.keys())
    pod_indexes.sort(key = lambda a: mysqlutils.count_gtids(gtids[a]))
    return pod_indexes[-1]

class ClusterMutex:
    def __init__(self, cluster: InnoDBCluster, pod: Optional[MySQLPod] = None, context: str = "n/a"):
        self.cluster = cluster
        self.pod = pod
        self.context = context

    def __enter__(self, *args):
        owner_lock_creation_time: datetime.datetime
        (owner, owner_context, owner_lock_creation_time) = utils.g_ephemeral_pod_state.testset(
            self.cluster, "cluster-mutex", self.pod.name if self.pod else self.cluster.name, context=self.context)
        if owner:
            raise kopf.TemporaryError(
                f"{self.cluster.name} busy. lock_owner={owner} owner_context={owner_context} lock_created_at={owner_lock_creation_time.isoformat()}", delay=10)

    def __exit__(self, *args):
        utils.g_ephemeral_pod_state.set(self.cluster, "cluster-mutex", None, context=self.context)


class ClusterController:
    """
    This is the controller for a innodbcluster object.
    It's the main controller for a cluster and drives the lifecycle of the
    cluster including creation, scaling and restoring from outages.
    """

    def __init__(self, cluster: InnoDBCluster):
        self.cluster = cluster
        self.dba: Optional[Dba] = None
        self.dba_cluster: Optional[Cluster] = None

    @property
    def dba_cluster_name(self) -> str:
        """Return the name of the cluster as defined in the k8s resource
        as a InnoDB Cluster compatible name."""
        return self.cluster.name.replace("-", "_").replace(".", "_")

    def publish_status(self, diag: diagnose.ClusterStatus) -> None:
        cluster_status = self.cluster.get_cluster_status()
        if cluster_status and cluster_status["status"] != diag.status.name:
            self.cluster.info(action="ClusterStatus", reason="StatusChange",
                              message=f"Cluster status changed to {diag.status.name}. {len(diag.online_members)} member(s) ONLINE")

        cluster_status = {
            "status": diag.status.name,
            "onlineInstances": len(diag.online_members),
            "lastProbeTime": utils.isotime()
        }
        self.cluster.set_cluster_status(cluster_status)

    def probe_status(self, logger: Logger) -> diagnose.ClusterStatus:
        diag = diagnose.diagnose_cluster(self.cluster, logger)
        if not self.cluster.deleting:
            self.publish_status(diag)
        logger.info(
            f"cluster probe: status={diag.status} online={diag.online_members}")
        return diag

    def probe_status_if_needed(self, changed_pod: MySQLPod, logger: Logger) -> diagnose.ClusterDiagStatus:
        cluster_probe_time = self.cluster.get_cluster_status("lastProbeTime")
        member_transition_time = changed_pod.get_membership_info(
            "lastTransitionTime")
        last_status = self.cluster.get_cluster_status("status")
        unreachable_states = (diagnose.ClusterDiagStatus.UNKNOWN,
                              diagnose.ClusterDiagStatus.ONLINE_UNCERTAIN,
                              diagnose.ClusterDiagStatus.OFFLINE_UNCERTAIN,
                              diagnose.ClusterDiagStatus.NO_QUORUM_UNCERTAIN,
                              diagnose.ClusterDiagStatus.SPLIT_BRAIN_UNCERTAIN)
        if cluster_probe_time and member_transition_time and cluster_probe_time < member_transition_time or last_status in unreachable_states:
            return self.probe_status(logger).status
        else:
            return last_status

    def probe_member_status(self, pod: MySQLPod, session: 'ClassicSession', joined: bool, logger) -> None:
        # TODO use diagnose?
        minfo = shellutils.query_membership_info(session)
        member_id, role, status, view_id, version, mcount, rmcount = minfo
        logger.debug(
            f"instance probe: role={role} status={status} view_id={view_id} version={version} members={mcount} reachable_members={rmcount}")
        pod.update_membership_status(
            member_id, role, status, view_id, version, joined=joined)
        # TODO
        if status == "ONLINE":
            pod.update_member_readiness_gate("ready", True)
        else:
            pod.update_member_readiness_gate("ready", False)

        return minfo

    def connect_to_primary(self, primary_pod: MySQLPod, logger: Logger) -> 'Cluster':
        if primary_pod:
            self.dba = shellutils.connect_dba(
                primary_pod.endpoint_co, logger, max_tries=2)
            self.dba_cluster = self.dba.get_cluster()
        else:
            # - check if we should consider pod marker for whether the instance joined
            self.connect_to_cluster(logger, need_primary=True)
        assert self.dba_cluster
        return self.dba_cluster

    def connect_to_cluster(self, logger: Logger, need_primary:bool = False) -> MySQLPod:
        # Get list of pods and try to connect to one of them
        def try_connect() -> MySQLPod:
            last_exc = None
            offline_pods = []
            all_pods = self.cluster.get_pods()
            for pod in all_pods:
                if pod.name in offline_pods or pod.deleting:
                    continue

                try:
                    self.dba = mysqlsh.connect_dba(pod.endpoint_co)

                    if need_primary:
                        res = self.dba.session.run_sql(
                            "SELECT member_role"
                            " FROM performance_schema.replication_group_members"
                            " WHERE member_host = @@report_host")

                        r = res.fetch_one()
                        if r[0] != "PRIMARY":
                            logger.info(f"Primary requested, but {pod.name} is no primary")
                            self.dba.session.close()
                            continue

                except Exception as e:
                    logger.debug(f"connect_dba: target={pod.name} error={e}")
                    # Try another pod if we can't connect to it
                    last_exc = e
                    continue

                try:
                    self.dba_cluster = self.dba.get_cluster()
                    logger.info(f"Connected to {pod}")
                    return pod
                except mysqlsh.Error as e:
                    logger.info(
                        f"get_cluster() from {pod.name} failed: {e}")

                    if e.code == errors.SHERR_DBA_BADARG_INSTANCE_NOT_ONLINE:
                        # This member is not ONLINE, so there's no chance of
                        # getting a cluster handle from it
                        offline_pods.append(pod.name)

                except Exception as e:
                    logger.info(
                        f"get_cluster() from {pod.name} failed: {e}")

            # If all pods are connectable but OFFLINE, then we have complete outage and need a reboot
            if len(offline_pods) == len(all_pods):
                raise kopf.TemporaryError(
                    "Could not connect to any cluster member", delay=15)

            if last_exc:
                raise last_exc

            raise kopf.TemporaryError(
                "Could not connect to any cluster member", delay=15)

        return try_connect()

    def log_mysql_info(self, pod: MySQLPod, session: 'ClassicSession', logger: Logger) -> None:
        row = session.run_sql(
            "select @@server_id, @@server_uuid, @@report_host").fetch_one()
        server_id, server_uuid, report_host = row
        try:
            row = session.run_sql(
                "select @@globals.gtid_executed, @@globals.gtid_purged").fetch_one()
            gtid_executed, gtid_purged = row
        except:
            gtid_executed, gtid_purged = None, None

        logger.info(
            f"server_id={server_id} server_uuid={server_uuid}  report_host={report_host}  gtid_executed={gtid_executed}  gtid_purged={gtid_purged}")

    def create_cluster(self, seed_pod: MySQLPod, logger: Logger) -> None:
        logger.info("Creating cluster at %s" % seed_pod.name)

        assume_gtid_set_complete = False
        initial_data_source = "blank"
        if self.cluster.parsed_spec.initDB:
            # TODO store version
            # TODO store last known quorum
            if self.cluster.parsed_spec.initDB.clone:
                initial_data_source = f"clone={self.cluster.parsed_spec.initDB.clone.uri}"
            elif self.cluster.parsed_spec.initDB.dump and seed_pod.index == 0: # A : Should we check for index?
                if self.cluster.parsed_spec.initDB.dump.storage.ociObjectStorage:
                    initial_data_source = f"dump={self.cluster.parsed_spec.initDB.dump.storage.ociObjectStorage.bucketName}"
                elif self.cluster.parsed_spec.initDB.dump.storage.s3:
                    initial_data_source = f"dump={self.cluster.parsed_spec.initDB.dump.storage.s3.bucketName}"
                elif self.cluster.parsed_spec.initDB.dump.storage.azure:
                    initial_data_source = f"dump={self.cluster.parsed_spec.initDB.dump.storage.azure.containerName}",
                elif self.cluster.parsed_spec.initDB.dump.storage.persistentVolumeClaim:
                    initial_data_source = f"dump={self.cluster.parsed_spec.initDB.dump.storage.persistentVolumeClaim}"
                else:
                    assert 0, "Unknown Dump storage mechanism"
            else:
                assert 0, "Unknown initDB source"
        else:
            # We're creating the cluster from scratch, so GTID set is sure to be complete
            assume_gtid_set_complete = True


        # The operator manages GR, so turn off start_on_boot to avoid conflicts
        create_options = {
            "gtidSetIsComplete": assume_gtid_set_complete,
            "manualStartOnBoot": True,
            "memberSslMode": "REQUIRED" if self.cluster.parsed_spec.tlsUseSelfSigned else "VERIFY_IDENTITY",
        }
        if not self.cluster.parsed_spec.tlsUseSelfSigned:
            logger.info("Using TLS GR authentication")
            rdns = seed_pod.get_cluster().get_tls_issuer_and_subject_rdns()
            create_options["memberAuthType"] = "CERT_SUBJECT"
            create_options["certIssuer"] = rdns["issuer"]
            create_options["certSubject"] = rdns["subject"]
        else:
            logger.info("Using PASSWORD GR authentication")

        create_options.update(common_gr_options)

        cluster_info = {
            "initialDataSource" : initial_data_source,
            "createOptions" : create_options,
        }
        self.cluster.update_cluster_info(cluster_info)

        def should_retry(err):
            if seed_pod.deleting:
                return False
            return True

        with DbaWrap(shellutils.connect_dba(seed_pod.endpoint_co, logger, is_retriable=should_retry)) as dba:
            try:
                self.dba_cluster = dba.get_cluster()
                # maybe from a previous incomplete create attempt
                logger.info("Cluster already exists")
            except:
                self.dba_cluster = None

            seed_pod.add_member_finalizer()

            if not self.dba_cluster:
                self.log_mysql_info(seed_pod, dba.session, logger)

                logger.info(f"CREATE CLUSTER: seed={seed_pod.name}, options={create_options}")

                try:
                    self.dba_cluster = dba.create_cluster(
                        self.dba_cluster_name, create_options)

                    logger.info("create_cluster OK")
                except mysqlsh.Error as e:
                    # If creating the cluster failed, remove the membership finalizer
                    seed_pod.remove_member_finalizer()

                    # can happen when retrying
                    if e.code == errors.SHERR_DBA_BADARG_INSTANCE_ALREADY_IN_GR:
                        logger.info(
                            f"GR already running at {seed_pod.endpoint}, stopping before retrying...")

                        try:
                            dba.session.run_sql("STOP GROUP_REPLICATION")
                        except mysqlsh.Error as e:
                            logger.info(f"Could not stop GR plugin: {e}")
                            # throw a temporary error for a full retry later
                            raise kopf.TemporaryError(
                                "GR already running while creating cluster but could not stop it", delay=3)
                    raise

            routing_options = self.cluster.parsed_spec.router.routingOptions
            for routing_option in routing_options:
                try:
                    routing_value = routing_options[routing_option]
                    self.dba_cluster.set_routing_option(routing_option, routing_value)
                except mysqlsh.Error as e:
                    # We don't fail when setting an option fails
                    logger.warn(f"Failed setting routing option {routing_option} to {routing_value}: {e}")

            self.probe_member_status(seed_pod, dba.session, True, logger)

            logger.debug("Cluster created %s" % self.dba_cluster.status())

            # if there's just 1 pod, then the cluster is ready... otherwise, we
            # need to wait until all pods have joined
            if self.cluster.parsed_spec.instances == 1:
                self.post_create_actions(dba.session, self.dba_cluster, logger)

    def post_create_actions(self, session: 'ClassicSession', dba_cluster: 'Cluster', logger: Logger) -> None:
        logger.info("cluster_controller::post_create_actions")
        # create router account
        user, password = self.cluster.get_router_account()

        update = True
        try:
            session.run_sql("show grants for ?@'%'", [user])
        except mysqlsh.Error as e:
            if e.code == mysqlsh.mysql.ErrorCode.ER_NONEXISTING_GRANT:
                update = False
            else:
                raise
        logger.debug(
            f"{'Updating' if update else 'Creating'} router account {user}")
        dba_cluster.setup_router_account(
            user, {"password": password, "update": update})

        # create backup account
        user, password = self.cluster.get_backup_account()
        logger.debug(f"Creating backup account {user}")
        mysqlutils.setup_backup_account(session, user, password)

        # update read replicas
        for rr in self.cluster.parsed_spec.readReplicas:
            logger.debug(f"Setting {rr.name} replicas to {rr.instances}")
            cluster_objects.update_stateful_set_size(self.cluster, rr, logger)

        # update the router deployment
        n = self.cluster.parsed_spec.router.instances
        if n:
            logger.debug(f"Setting router replicas to {n}")
            router_objects.update_size(self.cluster, n, logger)


    def reboot_cluster(self, seed_pod_index: MySQLPod, logger: Logger) -> None:
        pods = self.cluster.get_pods()
        seed_pod = pods[seed_pod_index]

        logger.info(f"Rebooting cluster {self.cluster.name} from pod {seed_pod}...")

        self.dba = shellutils.connect_dba(seed_pod.endpoint_co, logger)

        self.log_mysql_info(seed_pod, self.dba.session, logger)

        seed_pod.add_member_finalizer()

        self.dba_cluster = self.dba.reboot_cluster_from_complete_outage()

        logger.info(f"reboot_cluster_from_complete_outage OK.")

        # rejoin other pods
        for pod in pods:
            if pod.index != seed_pod_index:
                with shellutils.connect_to_pod(pod, logger, timeout=5) as session:
                    self.rejoin_instance(pod, session, logger)

        status = self.dba_cluster.status()
        logger.info(f"Cluster reboot successful. status={status}")

        self.probe_member_status(seed_pod, self.dba.session, True, logger)


    def force_quorum(self, seed_pod, logger: Logger) -> None:
        logger.info(
            f"Forcing quorum of cluster {self.cluster.name} using {seed_pod.name}...")

        self.connect_to_primary(seed_pod, logger)

        self.dba_cluster.force_quorum_using_partition_of(seed_pod.endpoint_co)

        status = self.dba_cluster.status()
        logger.info(f"Force quorum successful. status={status}")

        # TODO Rejoin OFFLINE members

    def destroy_cluster(self, last_pod, logger: Logger) -> None:
        logger.info(f"Stopping GR for last cluster member {last_pod.name}")

        try:
            with shellutils.connect_to_pod(last_pod, logger, timeout=5) as session:
                # Just stop GR
                session.run_sql("STOP group_replication")
        except Exception as e:
            logger.warning(
                f"Error stopping GR at last cluster member, ignoring... {e}")
            # Remove the pod membership finalizer even if we couldn't do final cleanup
            # (it's just stop GR, which should be harmless most of the time)
            last_pod.remove_member_finalizer()
            return

        logger.info("Stop GR OK")

        last_pod.remove_member_finalizer()

    def reconcile_pod(self, primary_pod: MySQLPod, pod: MySQLPod, logger: Logger) -> None:
        with DbaWrap(shellutils.connect_dba(pod.endpoint_co, logger)) as pod_dba_session:
            cluster = self.connect_to_primary(primary_pod, logger)

            status = diagnose.diagnose_cluster_candidate(
                self.dba.session, cluster, pod, pod_dba_session, logger)

            logger.info(
                f"Reconciling {pod}: state={status.status}  deleting={pod.deleting} cluster_deleting={self.cluster.deleting}")
            if pod.deleting or self.cluster.deleting:
                return

            # TODO check case where a member pod was deleted and then rejoins with the same address but different uuid

            if status.status == diagnose.CandidateDiagStatus.JOINABLE:
                self.cluster.info(action="ReconcilePod", reason="Join",
                                  message=f"Joining {pod.name} to cluster")
                self.join_instance(pod, pod_dba_session, logger)

            elif status.status == diagnose.CandidateDiagStatus.REJOINABLE:
                self.cluster.info(action="ReconcilePod", reason="Rejoin",
                                  message=f"Rejoining {pod.name} to cluster")
                self.rejoin_instance(pod, pod_dba_session.session, logger)

            elif status.status == diagnose.CandidateDiagStatus.MEMBER:
                logger.info(f"{pod.endpoint} already a member")

                self.probe_member_status(pod, pod_dba_session.session, False, logger)

            elif status.status == diagnose.CandidateDiagStatus.UNREACHABLE:
                # TODO check if we should throw a tmp error or do nothing
                logger.error(f"{pod.endpoint} is unreachable")

                self.probe_member_status(pod, pod_dba_session.session, False, logger)
            else:
                # TODO check if we can repair broken instances
                # It would be possible to auto-repair an instance with errant
                # transactions by cloning over it, but that would mean these
                # errants are lost.
                logger.error(f"{pod.endpoint} is in state {status.status}")

                self.probe_member_status(pod, pod_dba_session.session, False, logger)

    def join_instance(self, pod: MySQLPod, pod_dba_session: 'Dba', logger: Logger) -> None:
        logger.info(f"Adding {pod.endpoint} to cluster")

        peer_pod = self.connect_to_cluster(logger)

        self.log_mysql_info(pod, pod_dba_session.session, logger)

        # TODO - always use clone when dataset is big
        # With Shell Bug #33900165 fixed we should use "auto" by default
        # and remove the retry logic below
        recovery_method = "incremental"

        add_options = {
            "recoveryMethod": recovery_method,
        }

        # TODO : # add_replica_instance doesn't support cert base auth, thus certSubject works only for group-member-s - WL15056
        # If a cluster was created with cert based auth between the group members no replica can join the cluster
        for option in self.dba_cluster.options()["defaultReplicaSet"]["globalOptions"]:
            if option["option"] == "memberAuthType" and option["value"] in ["CERT_SUBJECT", "CERT_SUBJECT_PASSWORD"]:
                rdns = pod.get_cluster().get_tls_issuer_and_subject_rdns()
                # add_instance() needs only certSubject and but not memberAuthType and certIssuer
                add_options["certSubject"] = rdns["subject"]


        if pod.instance_type == "group-member":
            add_options.update(common_gr_options)

        logger.info(
            f"ADD INSTANCE: target={pod.endpoint}  instance_type={pod.instance_type} cluster_peer={peer_pod.endpoint}  options={add_options}...")

        pod.add_member_finalizer()

        try:
            if pod.instance_type == "read-replica":
                self.dba_cluster.add_replica_instance(pod.endpoint, add_options)
            else:
                self.dba_cluster.add_instance(pod.endpoint_co, add_options)

            logger.debug("add_instance OK")
        except  (mysqlsh.Error, RuntimeError) as e:
            logger.warning(f"add_instance failed: error={e}")

            # Incremetnal may fail if transactions are missing from binlog
            # retry using clone
            add_options["recoveryMethod"] = "clone"
            logger.warning(f"trying add_instance with clone")
            try:
                if pod.instance_type == "read-replica":
                    self.dba_cluster.add_replica_instance(pod.endpoint, add_options)
                else:
                    self.dba_cluster.add_instance(pod.endpoint_co, add_options)
            except (mysqlsh.Error, RuntimeError) as e:
                logger.warning(f"add_instance failed second time: error={e}")
                raise

        if pod.instance_type == "read-replica":
            # This is not perfect, as we don't track this further, but async
            # replication gives us limited information only
            pod.update_member_readiness_gate("ready", True)
        else:
            minfo = self.probe_member_status(pod, pod_dba_session.session, True, logger)

            member_id, role, status, view_id, version, member_count, reachable_member_count = minfo
            logger.info(f"JOINED {pod.name}: {minfo}")

        # if the cluster size is complete, ensure routers are deployed
        if not router_objects.get_size(self.cluster) and member_count == self.cluster.parsed_spec.instances:
            self.post_create_actions(self.dba.session, self.dba_cluster, logger)

    def rejoin_instance(self, pod: MySQLPod, pod_session, logger: Logger) -> None:
        logger.info(f"Rejoining {pod.endpoint} to cluster")

        if not self.dba_cluster:
            self.connect_to_cluster(logger)

        self.log_mysql_info(pod, pod_session, logger)

        rejoin_options = {}

        logger.info(
            f"rejoin_instance: target={pod.endpoint} options={rejoin_options}...")

        try:
            self.dba_cluster.rejoin_instance(pod.endpoint, rejoin_options)

            logger.debug("rejoin_instance OK")
        except mysqlsh.Error as e:
            logger.warning(f"rejoin_instance failed: error={e}")
            raise

        self.probe_member_status(pod, pod_session, False, logger)

    def remove_instance(self, pod: MySQLPod, pod_body: Body, logger: Logger, force: bool = False) -> None:
        try:
            self.__remove_instance_aux(pod, logger, force)
        except Exception as e:
            logger.info(f"Exception {e} caught")
            pass
        finally:
            # Remove the membership finalizer to allow the pod to be removed
            pod.remove_member_finalizer(pod_body)
            logger.info(f"Removed finalizer for pod {pod_body['metadata']['name']}")

    def __remove_instance_aux(self, pod: MySQLPod, logger: Logger, force: bool = False) -> None:
        print(f"Removing {pod.endpoint} from cluster FORCE={force}")

        # TODO improve this check
        other_pods = self.cluster.get_pods()
        if len(other_pods) == 1 and pod.instance_type == 'group-member':
            print("There is only one pod left in the cluster. Won't remove it, as this will dissolve the cluster. It will be removed only if the cluster is being deleted.")

        if len(other_pods) > 1 or (len(other_pods) > 0 and pod.instance_type == 'read-replica'):
            try:
                print("connect_to_cluster")
                peer_pod = self.connect_to_cluster(logger)
                print(f"peer_pod={peer_pod}")
            except mysqlsh.Error as e:
                peer_pod = None
                if self.cluster.deleting:
                    logger.warning(
                        f"Could not connect to cluster, but ignoring because we're deleting: error={e}")
                else:
                    logger.error(f"Could not connect to cluster: error={e}")
                    raise

            if peer_pod:
                removed = False
                remove_options = {}

                if not force:
                    logger.info(
                        f"remove_instance: {pod.name}  peer={peer_pod.name}  options={remove_options}")
                    try:
                        self.dba_cluster.remove_instance(pod.endpoint, remove_options)
                        removed = True
                        logger.debug("remove_instance OK")
                    except mysqlsh.Error as e:
                        logger.warning(f"remove_instance failed: error={e}")
                        if e.code == mysqlsh.mysql.ErrorCode.ER_OPTION_PREVENTS_STATEMENT:
                            # super_read_only can still be true on a PRIMARY for a
                            # short time
                            raise kopf.TemporaryError(
                                f"{peer_pod.name} is a PRIMARY but super_read_only is ON", delay=5)
                        elif e.code == errors.SHERR_DBA_MEMBER_METADATA_MISSING:
                            # already removed and we're probably just retrying
                            removed = True
                print(f"removed={removed}")
                if not removed:
                    remove_options["force"] = True
                    logger.info(
                        f"remove_instance: {pod.name}  peer={peer_pod.name}  options={remove_options}")
                    try:
                        self.dba_cluster.remove_instance(pod.endpoint, remove_options)

                        logger.info("FORCED remove_instance OK")
                    except mysqlsh.Error as e:
                        logger.warning(f"remove_instance failed: error={e}")
                        if e.code == errors.SHERR_DBA_MEMBER_METADATA_MISSING:
                            pass
                        else:
                            deleting = not self.cluster or self.cluster.deleting
                            if deleting:
                                logger.info(
                                    f"force remove_instance failed. Ignoring because cluster is deleted: error={e}  peer={peer_pod.name}")
                            else:
                                logger.error(
                                    f"force remove_instance failed. error={e} deleting_cluster={deleting}  peer={peer_pod.name}")
                                raise
                    except RuntimeError as e:
                        logger.info(f"force remove_instance failed. RuntimeError {e}")
                        if str(e).find("The cluster object is disconnected") == -1:
                            logger.info(f"Can't do anything to remove {pod.name} cleanly")
                            raise
            else:
                logger.error(
                    f"Cluster is not available, skipping clean removal of {pod.name}")



    def repair_cluster(self, pod: MySQLPod, diagnostic: diagnose.ClusterStatus, logger: Logger) -> None:
        # TODO check statuses where router has to be put down

        # Restore cluster to an ONLINE state
        if diagnostic.status == diagnose.ClusterDiagStatus.ONLINE:
            # Nothing to do
            return

        elif diagnostic.status == diagnose.ClusterDiagStatus.ONLINE_PARTIAL:
            # Nothing to do, rejoins handled on pod events
            return

        elif diagnostic.status == diagnose.ClusterDiagStatus.ONLINE_UNCERTAIN:
            # Nothing to do
            # TODO maybe delete unreachable pods if enabled?
            return

        elif diagnostic.status == diagnose.ClusterDiagStatus.OFFLINE:
            # Reboot cluster if all pods are reachable
            if len([g for g in diagnostic.gtid_executed.values() if g is not None]) == len(self.cluster.get_pods()):
                seed_pod = select_pod_with_most_gtids(diagnostic.gtid_executed)

                self.cluster.info(action="RestoreCluster", reason="Rebooting",
                                    message=f"Restoring OFFLINE cluster through pod {seed_pod}")

                shellutils.RetryLoop(logger).call(self.reboot_cluster, seed_pod, logger)
            else:
                logger.debug(f"Cannot reboot cluster because not all pods are reachable")
                raise kopf.TemporaryError(
                        f"Cluster cannot be restored because there are unreachable pods", delay=5)

        elif diagnostic.status == diagnose.ClusterDiagStatus.OFFLINE_UNCERTAIN:
            # TODO delete unconnectable pods after timeout, if enabled
            raise kopf.TemporaryError(
                f"Unreachable members found while in state {diagnostic.status}, waiting...")

        elif diagnostic.status == diagnose.ClusterDiagStatus.NO_QUORUM:
            # Restore cluster
            self.cluster.info(action="RestoreCluster", reason="RestoreQuorum",
                              message="Restoring quorum of cluster")

            shellutils.RetryLoop(logger).call(
                self.force_quorum, diagnostic.quorum_candidates[0], logger)

        elif diagnostic.status == diagnose.ClusterDiagStatus.NO_QUORUM_UNCERTAIN:
            # Restore cluster
            # TODO delete unconnectable pods after timeout, if enabled
            raise kopf.TemporaryError(
                f"Unreachable members found while in state {diagnostic.status}, waiting...")

        elif diagnostic.status == diagnose.ClusterDiagStatus.SPLIT_BRAIN:
            self.cluster.error(action="UnrecoverableState", reason="SplitBrain",
                               message="Cluster is in a SPLIT-BRAIN state and cannot be restored automatically.")

            # TODO check if recoverable case
            # Fatal error, user intervention required
            raise kopf.PermanentError(
                f"Unable to recover from current cluster state. User action required. state={diagnostic.status}")

        elif diagnostic.status == diagnose.ClusterDiagStatus.SPLIT_BRAIN_UNCERTAIN:
            # TODO check if recoverable case and if NOT, then throw a permanent error
            self.cluster.error(action="UnrecoverableState", reason="SplitBrain",
                               message="Cluster is in state SPLIT-BRAIN with unreachable instances and cannot be restored automatically.")

            raise kopf.PermanentError(
                f"Unable to recover from current cluster state. User action required. state={diagnostic.status}")
            # TODO delete unconnectable pods after timeout, if enabled
            raise kopf.TemporaryError(
                f"Unreachable members found while in state {diagnostic.status}, waiting...")

        elif diagnostic.status == diagnose.ClusterDiagStatus.UNKNOWN:
            # Nothing to do, but we can try again later and hope something comes back
            raise kopf.TemporaryError(
                f"No members of the cluster could be reached. state={diagnostic.status}")

        elif diagnostic.status == diagnose.ClusterDiagStatus.INVALID:
            self.cluster.error(action="UnrecoverableState", reason="Invalid",
                               message="Cluster state is invalid and cannot be restored automatically.")

            raise kopf.PermanentError(
                f"Unable to recover from current cluster state. User action required. state={diagnostic.status}")

        elif diagnostic.status == diagnose.ClusterDiagStatus.FINALIZING:
            # Nothing to do
            return

        else:
            raise kopf.PermanentError(
                f"Invalid cluster state {diagnostic.status}")


    def on_router_tls_changed(self) -> None:
        """
        Router pods need to be recreated in order for new certificates to get
        reloaded.
        """
        pass

    def on_pod_created(self, pod: MySQLPod, logger: Logger) -> None:
        print("on_pod_created: probing cluster")
        diag = self.probe_status(logger)

        print(f"on_pod_created: pod={pod.name} primary={diag.primary} cluster_state={diag.status}")

        if diag.status == diagnose.ClusterDiagStatus.INITIALIZING:
            # If cluster is not yet created, then we create it at pod-0
            if pod.index == 0:
                if self.cluster.get_create_time():
                    raise kopf.PermanentError(
                        f"Internal inconsistency: cluster marked as initialized, but create requested again")

                print("Time to create the cluster")
                shellutils.RetryLoop(logger).call(self.create_cluster, pod, logger)

                # Mark the cluster object as already created
                self.cluster.set_create_time(datetime.datetime.now())
            else:
                # Other pods must wait for the cluster to be ready
                raise kopf.TemporaryError("Cluster is not yet ready", delay=15)

        elif diag.status in (diagnose.ClusterDiagStatus.ONLINE, diagnose.ClusterDiagStatus.ONLINE_PARTIAL, diagnose.ClusterDiagStatus.ONLINE_UNCERTAIN):
            print("Reconciling pod")
            # Cluster exists and is healthy, join the pod to it
            shellutils.RetryLoop(logger).call(
                self.reconcile_pod, diag.primary, pod, logger)
        else:
            print("Attempting to repair the cluster")
            self.repair_cluster(pod, diag, logger)

            # Retry from scratch in another iteration
            raise kopf.TemporaryError(f"Cluster repair from state {diag.status} attempted", delay=5)

    def on_pod_restarted(self, pod: MySQLPod, logger: Logger) -> None:
        diag = self.probe_status(logger)
        logger.debug(
            f"on_pod_restarted: pod={pod.name}  primary={diag.primary}  cluster_state={diag.status}")

        if diag.status not in (diagnose.ClusterDiagStatus.ONLINE, diagnose.ClusterDiagStatus.ONLINE_PARTIAL):
            self.repair_cluster(pod, diag, logger)

        shellutils.RetryLoop(logger).call(
            self.reconcile_pod, diag.primary, pod, logger)

    def on_pod_deleted(self, pod: MySQLPod, pod_body: Body, logger: Logger) -> None:
        diag = self.probe_status(logger)

        print(f"on_pod_deleted: pod={pod.name}  primary={diag.primary}  cluster_state={diag.status} cluster.deleting={self.cluster.deleting}")

        if self.cluster.deleting:
            # cluster is being deleted, if this is pod-0 shut it down
            if pod.index == 0:
                self.destroy_cluster(pod, logger)
                pod.remove_member_finalizer(pod_body)
                return

        if pod.deleting and diag.status in (diagnose.ClusterDiagStatus.ONLINE, diagnose.ClusterDiagStatus.ONLINE_PARTIAL, diagnose.ClusterDiagStatus.ONLINE_UNCERTAIN, diagnose.ClusterDiagStatus.FINALIZING):
            print(f"REMOVING INSTANCE {pod.name}")
            shellutils.RetryLoop(logger).call(
                self.remove_instance, pod, pod_body, logger)
        else:
            print("ATTEMPTING CLUSTER REPAIR")
            self.repair_cluster(pod, diag, logger)
            # Retry from scratch in another iteration
            print("RETRYING ON POD DELETE")
            raise kopf.TemporaryError(f"Cluster repair from state {diag.status} attempted", delay=3)

        # TODO maybe not needed? need to make sure that shrinking cluster will be reported as ONLINE
        self.probe_status(logger)

    def on_group_view_change(self, members: list[tuple], view_id_changed) -> None:
        """
        Query membership info about the cluster and update labels and
        annotations in each pod.

        This is for monitoring only and should not trigger any changes other
        than in informational k8s fields.
        """
        for pod in self.cluster.get_pods():
            info = pod.get_membership_info()
            if info:
                pod_member_id = info.get("memberId")
            else:
                pod_member_id = None

            for member_id, role, status, view_id, endpoint, version in members:
                if pod_member_id and member_id == pod_member_id:
                    pass
                elif endpoint == pod.endpoint:
                    pass
                else:
                    continue
                pod.update_membership_status(
                    member_id, role, status, view_id, version)
                if status == "ONLINE":
                    pod.update_member_readiness_gate("ready", True)
                else:
                    pod.update_member_readiness_gate("ready", False)
                break

    def on_server_image_change(self, version: str) -> None:
        return self.on_upgrade(version = version)

    def on_server_version_change(self, version: str) -> None:
        return self.on_upgrade(version = version)

    def on_upgrade(self, version: str) -> None:
        # TODO change status as needed - especially on version error, but make sure we recover
        [version_valid, version_error] = utils.version_in_range(version)
        if not version_valid:
            raise kopf.PermanentError(version_error)

    def on_router_upgrade(self, logger: Logger) -> None:
        def on_nonupdated() -> None:
            raise kopf.TemporaryError(f"Cluster {self.cluster.namespace}/{self.cluster.name} unreachable", delay=5)
        router_objects.update_router_account(self.cluster, on_nonupdated, logger)

    def on_change_metrics_user(self, logger: Logger) -> None:
        metrics = self.cluster.parsed_spec.metrics
        self.connect_to_primary(None, logger)

        if not metrics or not metrics.enable:
            # This will use default name. needs to adapt when supporting custom
            # names
            mysqlutils.remove_metrics_user(self.dba.session)
            return

        user = metrics.dbuser_name
        grants = metrics.dbuser_grants
        max_connections = metrics.dbuser_max_connections

        mysqlutils.setup_metrics_user(self.dba.session, user, grants,
                                      max_connections)

    def on_router_routing_option_chahnge(self, old: dict, new: dict, logger: Logger) -> None:
        self.connect_to_primary(None, logger)

        # Unset removed entries
        for key in old:
            if key not in new:
                try:
                    self.dba_cluster.set_routing_option(key, None)
                except mysqlsh.Error as e:
                    # We don't fail when setting an option fails
                    logger.warn(f"Failed unsetting routing option {key}: {e}")

        # Set new values, this resets existing values
        for key in new:
            try:
                self.dba_cluster.set_routing_option(key, new[key])
            except mysqlsh.Error as e:
                # We don't fail when setting an option fails
                logger.warn(f"Failed setting routing option {key} to {new[key]}: {e}")
