# Copyright (c) 2020, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# check state of group


from utils import tutil
from utils import mutil
import json
from setup.config import g_ts_cfg


def check_group(test, icobj, all_pods, user="root", password="sakila"):
    info = {}

    with mutil.MySQLPodSession(icobj["metadata"]["namespace"], icobj["metadata"]["name"]+"-0", user, password) as session:
        members = session.query_sql(
            "SELECT member_id, member_host, member_port, member_state, member_role FROM performance_schema.replication_group_members ORDER BY member_host").fetch_all()
        test.assertEqual(len(members), icobj["spec"]["instances"])

        test.assertEqual(icobj["status"]["cluster"]
                         ["onlineInstances"], len(members))

        tutil.logger.debug(members)

        primaries = []
        for mid, mhost, mport, mstate, mrole in members:
            test.assertEqual(mstate, "ONLINE", f"{mhost}:{mport}")
            if mrole == "PRIMARY":
                primaries.append(mrole)
                info["primary"] = int(mhost.split(".")[0].split("-")[-1])

        test.assertEqual(len(primaries), 1)

    return info


def check_instance(test, icobj, all_pods, pod, is_primary, num_sessions=None, version=None, user="root", password="sakila"):
    group_seeds = set()
    for p in all_pods:
        if p != pod:
            group_seeds.add(p["metadata"]["name"]+"."+icobj["metadata"]["name"] +
                            "-instances."+icobj["metadata"]["namespace"]+".svc.cluster.local:3306")

    name = pod["metadata"]["name"]
    base_id = icobj["spec"].get("baseServerId", 1000)

    with mutil.MySQLPodSession(pod["metadata"]["namespace"], pod["metadata"]["name"], user, password) as session:
        # check that the Pod info matches
        server_id, server_uuid, report_host, sro, ver = session.query_sql(
            "select @@server_id, @@server_uuid, @@report_host, @@super_read_only, @@version").fetch_one()
        if is_primary:
            test.assertFalse(sro, f"{name} PRIMARY sro=0")
        else:
            test.assertTrue(sro, f"{name} SECONDARY sro=1")

        # membership-info can be missing if the instance is deleted
        # we check elsewhere if it's supposed to be there, so it's safe to ignore it here
        minfo = pod["metadata"]["annotations"].get(
            "mysql.oracle.com/membership-info")
        if minfo:
            minfo = json.loads(minfo)
            test.assertEqual(minfo["memberId"], server_uuid, name)
            test.assertEqual(int(pod["metadata"]["name"].split(
                "-")[-1]) + base_id, server_id, name)

        if version:
            if "-" in version:
                test.assertEqual(ver, version, name)
            else:
                test.assertEqual(ver.split("-")[0], version, name)

        # check that the GR config is as expected
        grvars = dict(session.query_sql(
            "show global variables like 'group_replication%'").fetch_all())

        # mysqlsh < 8.0.27 was not handling start_on_boot correctly
        if g_ts_cfg.operator_shell_version_num >= 80027:
            test.assertEqual(
                grvars["group_replication_start_on_boot"], "OFF", name)
        test.assertEqual(
            grvars["group_replication_single_primary_mode"], "ON", name)
        test.assertEqual(
            grvars["group_replication_bootstrap_group"], "OFF", name)

        if len(all_pods) == 1:
            test.assertEqual(grvars["group_replication_group_seeds"], "", name)
        else:
            # remove self from the group_seeds, which is unexpected, but not necessarily bad
            my_group_seeds = [m for m in grvars["group_replication_group_seeds"].strip(
                ", ").split(",") if not m.startswith(name)]
            test.assertSetEqual(set(my_group_seeds), group_seeds, name)

        # check that SSL is enabled for recovery
        if not is_primary:
            row = session.query_sql(
                "select ssl_allowed, coalesce(tls_version, '') from performance_schema.replication_connection_configuration where channel_name='group_replication_recovery'").fetch_one()
            # there's no recovery channel in the seed
            if row:
                ssl_allowed, tls_version = row
                test.assertTrue(ssl_allowed, name)
                test.assertNotEqual(tls_version, "", name)

        if num_sessions is not None:
            sessions = [tuple(row)
                        for row in session.query_sql("show processlist").fetch_all()
                        if row["User"] not in ("event_scheduler", "system user")
                        and row["Command"] not in ("Binlog Dump GTID", )]
            test.assertEqual(len(sessions), num_sessions+1, repr(sessions))


def schema_report(session, schema):
    table_info = {}
    for table, in session.query_sql("SHOW TABLES IN %s" % (schema,)).fetch_all():
        count = session.query_sql("SELECT count(*) FROM %s.%s" % (schema, table)).fetch_one()[0]

        checksum = session.query_sql("CHECKSUM TABLE %s.%s" %(schema, table)).fetch_one()[0]

        table_info[table] = {"rows": count, "checksum": checksum}
    return table_info


def check_data(test, all_pods, user="root", password="sakila", primary=0):
    ignore_schemas = ("mysql", "information_schema",
                      "performance_schema", "sys")

    assert all_pods[primary]["metadata"]["name"].endswith(str(primary))

    with mutil.MySQLPodSession(
            all_pods[primary]["metadata"]["namespace"], all_pods[primary]["metadata"]["name"],
            user, password) as session0:
        gtid_set0 = session0.query_sql("SELECT @@gtid_executed").fetch_one()[0]

        schemas0 = set([r[0]
                        for r in session0.query_sql("SHOW SCHEMAS").fetch_all()])

        schema_table_info0 = {}
        for schema in schemas0:
            if schema not in ignore_schemas:
                schema_table_info0[schema] = schema_report(session0, schema)

        for i, pod in enumerate(all_pods):
            if i == primary:
                continue
            with mutil.MySQLPodSession(
                    pod["metadata"]["namespace"], pod["metadata"]["name"],
                    user, password) as s:
                r = s.query_sql(
                    "select WAIT_FOR_EXECUTED_GTID_SET(%s, 0)", (gtid_set0, ))
                test.assertEqual(r.fetch_one()[0], 0)

                # check for missing GTIDs
                missing = s.query_sql("select gtid_subtract(%s, @@gtid_executed)", (gtid_set0, )).fetch_one()[0]
                test.assertEqual("", missing, pod["metadata"]["name"])

                # check for errant GTIDs comparing against a more recent GTID set from primary
                gtid_set = s.query_sql("select @@gtid_executed").fetch_one()[0]
                errants = session0.query_sql("select gtid_subtract(%s, @@gtid_executed)", (gtid_set, )).fetch_one()[0]
                test.assertEqual("", errants, pod["metadata"]["name"])

                test.assertSetEqual(schemas0, set([r[0]
                                                   for r in s.query_sql("SHOW SCHEMAS").fetch_all()]),
                                    pod["metadata"]["name"])

                for schema in schemas0:
                    if schema not in ignore_schemas:
                        table_info = schema_report(s, schema)

                        test.assertEqual(schema_table_info0[schema], table_info,
                                         pod["metadata"]["name"])
