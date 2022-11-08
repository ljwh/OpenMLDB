# Copyright 2021 4Paradigm
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import logging

log = logging.getLogger(__name__)
import sys
from tool import Executor
from tool import Partition
from tool import Status
import time
from optparse import OptionParser
import random
parser = OptionParser()

parser.add_option("--openmldb_bin_path",
                  dest="openmldb_bin_path",
                  help="the openmldb bin path")

parser.add_option("--zk_cluster", 
                  dest="zk_cluster",
                  help="the zookeeper cluster")

parser.add_option("--zk_root_path",
                  dest="zk_root_path",
                  help="the zookeeper root path")

parser.add_option("--cmd",
                  dest="cmd",
                  help="cmd")

parser.add_option("--endpoints",
                  dest="endpoints",
                  help="endpoints")

INTERNAL_DB = ["__INTERNAL_DB", "__PRE_AGG_DB", "INFORMATION_SCHEMA"]

def CheckTable(executor : Executor, db, table_name):
    status, table_partitions = executor.GetTablePartition(db, table_name)
    if not status.OK():
        return status
    endpoints = set()
    for pid, partitions in table_partitions.items():
        for partition in partitions:
            endpoints.add(partition.GetEndpoint())
            if not partition.IsAlive():
                return Status(-1, f"partition is not alive. {db} {table_name} {pid} {partition.GetEndpoint()}")
    endpoint_status = {}
    for endpoint in endpoints:
        status, result = executor.GetTableStatus(endpoint)
        if not status.OK():
            return status
        endpoint_status[endpoint] = result
    for pid, partitions in table_partitions.items():
        for partition in partitions:
            endpoint = partition.GetEndpoint()
            key = partition.GetKey()
            if endpoint not in endpoint_status or key not in endpoint_status[endpoint]:
                return Status(-1, f"not table partition in {endpoint}")
            if partition.IsLeader() and endpoint_status[endpoint][key][3] == "kTableLeader":
                continue
            elif not partition.IsLeader() and endpoint_status[endpoint][key][3] != "kTableLeader":
                continue
            return Status(-1, f"role is not match")
    return Status()

def RecoverPartition(executor : Executor, db, partitions : list, endpoint_status : dict):
    leader_pos = -1
    max_offset = 0
    table_name = partitions[0].GetName()
    pid = partitions[0].GetPid()
    for pos in range(len(partitions)):
        partition = partitions[pos]
        if partition.IsLeader() and partition.GetOffset() >= max_offset:
            leader_pos = pos
    if leader_pos < 0:
        log.error(f"cannot find leader partition. db {db} name {table_name} partition {pid}")
        return Status(-1, "recover partition failed")
    tid = partitions[0].GetTid()
    leader_endpoint = partitions[leader_pos].GetEndpoint()
    # recover leader
    if f"{tid}_{pid}" not in endpoint_status[leader_endpoint]:
        log.info(f"leader partition is not in tablet, db {db} name {table_name} pid {pid} endpoint {leader_endpoint}. start loading data...")
        if not executor.LoadTable(leader_endpoint, table_name, tid, pid).OK():
            log.error(f"load table failed. db {db} name {table_name} pid {pid} endpoint {leader_endpoint}")
            return Status(-1, "recover partition failed")
    if not partitions[leader_pos].IsAlive():
        status =  executor.UpdateTableAlive(db, table_name, pid, leader_endpoint, "yes")
        if not status.OK():
            log.error(f"update leader alive failed. db {db} name {table_name} pid {pid} endpoint {leader_endpoint}")
            return Status(-1, "recover partition failed")
    # recover follower
    for pos in range(len(partitions)):
        if pos == leader_pos:
            continue
        partition = partitions[pos]
        endpoint = partition.GetEndpoint()
        if partition.IsAlive():
            status = executor.UpdateTableAlive(db, table_name, pid, endpoint, "no")
            if not status.OK():
                log.error(f"update alive failed. db {db} name {table_name} pid {pid} endpoint {endpoint}")
                return Status(-1, "recover partition failed")
        if not executor.RecoverTablePartition(db, table_name, pid, endpoint).OK():
            log.error(f"recover table partition failed. db {db} name {table_name} pid {pid} endpoint {endpoint}")
            return Status(-1, "recover table partition failed")

def RecoverTable(executor : Executor, db, table_name) -> Status:
    if CheckTable(executor, db, table_name).OK():
        log.info(f"{table_name} in {db} is healthy")
        return Status()
    log.info(f"recover {table_name} in {db}")
    status, table_info = executor.GetTableInfo(db, table_name)
    if not status.OK():
        log.warn(f"get table info failed. msg is {status.GetMsg()}")
        return Status(-1, f"get table info failed. msg is {status.GetMsg()}")
    partition_dict = executor.ParseTableInfo(table_info)
    endpoints = set()
    for record in table_info:
        endpoints.add(record[3])
    endpoint_status = {}
    for endpoint in endpoints:
        status, result = executor.GetTableStatus(endpoint)
        if not status.OK():
            log.warn(f"get table status failed. msg is {status.GetMsg()}")
            return Status(-1, f"get table status failed. msg is {status.GetMsg()}")
        endpoint_status[endpoint] = result
    max_pid = int(table_info[-1][2])
    for pid in range(max_pid + 1):
        RecoverPartition(executor, db, partition_dict[str(pid)], endpoint_status)
    # wait op
    while True:
        status, result = executor.ShowOpStatus(db, table_name)
        is_finish = True
        if status.OK():
            for record in result:
                if record[4] == 'kDoing':
                    value = " ".join(record)
                    log.info(value)
                    is_finish = False
                    break
        if not is_finish:
            log.info(f"waiting task")
            time.sleep(2)
        else:
            break
    status = CheckTable(executor, db, table_name)
    if status.OK():
        log.info(f"{table_name} in {db} recover success")
    else:
        log.warn(status.GetMsg())
    return status

def RecoverData(executor : Executor):
    status, dbs = executor.GetAllDatabase()
    if not status.OK():
        log.error("get database failed")
        return
    alldb = list(INTERNAL_DB)
    alldb.extend(dbs)
    for db in alldb:
        status, tables = executor.GetAllTable(db)
        if not status.OK():
            log.error(f"get all table failed")
            return
        for name in tables:
            if not RecoverTable(executor, db, name).OK():
                return

def MigratePartition(db : str, partition : Partition, src_endpoint : str, desc_endpoint : str, one_replica : bool) -> Status:
    if partition.IsLeader():
        if one_replica and not executor.AddReplica(db, partition.GetName(), partition.GetPid(), desc_endpoint, True).OK():
            return Status(-1, f"add replica failed. {db} {partition.GetName()} {partition.GetPid()} {desc_endpoint}")
        if not executor.ChangeLeader(db, partition.GetName(), partition.GetPid(), True).OK():
            return Status(-1, f"change leader failed. {db} {partition.GetName()} {partition.GetPid()}")
        status = executor.RecoverTablePartition(db, partition.GetName(), partition.GetPid(), src_endpoint, True)
        if not status.OK():
            log.error(status.GetMsg())
            return Status(-1, f"recover table failed. {db} {partition.GetName()} {partition.GetPid()} {src_endpoint}")
    if one_replica:
        if not executor.DelReplica(db, partition.GetName(), partition.GetPid(), src_endpoint, True).OK():
            return Status(-1, f"del replica failed. {db} {partition.GetName()} {partition.GetPid()} {src_endpoint}")
    else:
        status = executor.Migrate(db, partition.GetName(), partition.GetPid(), src_endpoint, desc_endpoint, True)
        if not status.OK():
            log.error(status.GetMsg())
            return Status(-1, f"migrate partition failed! table {partition.GetName()} partition {partition.GetPid()} {src_endpoint} {desc_endpoint}")
    return Status()

def BalanceInDatabase(executor : Executor, endpoints : list, db : str) -> Status:
    log.info(f"start to balance {db}")
    status, result = executor.GetTableInfo(db)
    if not status.OK():
        log.error("get table failed from {db}")
        return Status(-1, "get table failed from {db}")
    all_dict : dict[str, list[Partition]] = {}
    total_partitions = 0
    endpoint_partition_map : dict[str, set] = {}
    replica_map = {}
    for record in result:
        total_partitions += 1
        is_leader = True if record[4] == "leader" else False
        is_alive = True if record[5] == "yes" else False
        partition : Partition = Partition(record[0], record[1], record[2], record[3], is_leader, is_alive, int(record[6]))
        all_dict.setdefault(partition.GetEndpoint(), []);
        all_dict[partition.GetEndpoint()].append(partition)
        endpoint_partition_map.setdefault(partition.GetEndpoint(), set())
        endpoint_partition_map[partition.GetEndpoint()].add(partition.GetKey())
        replica_map.setdefault(partition.GetKey(), 0)
        replica_map[partition.GetKey()] += 1
    for endpoint in endpoints:
        if endpoint not in all_dict:
            all_dict.setdefault(endpoint, []);
            endpoint_partition_map.setdefault(endpoint, set())

    start_endpoint = random.choice(endpoints)
    while True:
        migrate_out_endpoint = start_endpoint;
        migrate_in_endpoint = start_endpoint;
        for endpoint in endpoints:
            if len(all_dict[endpoint]) > len(all_dict[migrate_out_endpoint]) : migrate_out_endpoint = endpoint
            if len(all_dict[endpoint]) < len(all_dict[migrate_in_endpoint]) : migrate_in_endpoint = endpoint
        log.info(f"max partition endpoint: {migrate_out_endpoint} num: {len(all_dict[migrate_out_endpoint])}, "
                 f"min partition endpoint: {migrate_in_endpoint} num: {len(all_dict[migrate_in_endpoint])}")
        if not len(all_dict[migrate_out_endpoint]) > len(all_dict[migrate_in_endpoint]) + 1 : break
        candidate_partition = list(all_dict[migrate_out_endpoint])
        while len(candidate_partition) > 0:
            idx = random.randint(0, len(candidate_partition) - 1)
            partition : Partition = candidate_partition.pop(idx)
            if partition.GetKey() in endpoint_partition_map[migrate_in_endpoint]:
                continue
            log.info(f"migrate table {partition.GetName()} partition {partition.GetPid()} in {db} from {partition.GetEndpoint()} to {migrate_in_endpoint}")
            status = MigratePartition(db, partition, migrate_out_endpoint, migrate_in_endpoint, replica_map[partition.GetKey()] == 1)
            if not status.OK():
                log.error(status.GetMsg())
                return status
            log.info(f"migrate table {partition.GetName()} partition {partition.GetPid()} in {db} from {partition.GetEndpoint()} to {migrate_in_endpoint} success")
            all_dict[migrate_in_endpoint].append(partition)
            endpoint_partition_map[migrate_in_endpoint].add(partition.GetKey())
            for pos in range(len(all_dict[migrate_out_endpoint])):
                if all_dict[migrate_out_endpoint][pos].GetKey() == partition.GetKey():
                    del all_dict[migrate_out_endpoint][pos]
                    break
            endpoint_partition_map[migrate_out_endpoint].remove(partition.GetKey())
            break
    return Status()

def ScaleOut(executor : Executor):
    status, result = executor.ShowTablet()
    if not status.OK():
        log.error(f"execute showtablet failed")
        return
    endpoints = []
    for record in result:
        if record[2] == "kHealthy" : endpoints.append(record[0])
    status, dbs = executor.GetAllDatabase()
    if not status.OK():
        log.error("get database failed")
        return
    for db in dbs:
        if not BalanceInDatabase(executor, endpoints, db).OK():
            return
    log.info("execute scale-out success")

def ScaleInEndpoint(executor : Executor, endpoint : str, desc_endpoints : list) -> Status:
    log.info(f"start to scale-in {endpoint}")
    status, status_result = executor.GetTableStatus(endpoint)
    if not status.OK():
        log.error(f"get table status failed from {endpoint}")
        return Status(-1, f"get table status failed from {endpoint}")
    status, user_dbs = executor.GetAllDatabase()
    if not status.OK():
        log.error("get data base failed")
        return Status(-1, "get data base failed")
    dbs = list(INTERNAL_DB)
    dbs.extend(user_dbs)
    all_dict : dict[str, list[Partition]] = {}
    endpoint_partition_map : dict[str, set] = {}
    db_map = {}
    replica_map = {}
    for db in dbs:
        status, result = executor.GetTableInfo(db)
        if not status.OK():
            log.error("get table failed")
            return Status(-1, "get table failed")
        for record in result:
            is_leader = True if record[4] == "leader" else False
            is_alive = True if record[5] == "yes" else False
            partition : Partition = Partition(record[0], record[1], record[2], record[3], is_leader, is_alive, int(record[6]))
            all_dict.setdefault(partition.GetEndpoint(), [])
            all_dict[partition.GetEndpoint()].append(partition)
            endpoint_partition_map.setdefault(partition.GetEndpoint(), set())
            endpoint_partition_map[partition.GetEndpoint()].add(partition.GetKey())
            db_map.setdefault(partition.GetKey(), (db, partition.GetName()))
            replica_map.setdefault(partition.GetKey(), 0)
            replica_map[partition.GetKey()] += 1
    for key, value in replica_map.items():
        if value > len(desc_endpoints):
            db, name = db_map[key]
            log.error(f"replica num of table {name} in {db} is {value}, left endpoints num is {len(desc_endpoints)}, cannot execute scale-in")
            return Status(-1, "cannot execute scale-in")
    for key, record in status_result.items():
        is_leader = True if record[3] == "kTableLeader" else False
        db, name = db_map.get("{}_{}".format(record[0], record[1]))
        partition : Partition = Partition(name, record[0], record[1], endpoint, is_leader, True, int(record[2]))
        desc_endpoint = ""
        min_partition_num = sys.maxsize
        for cur_endpoint in all_dict:
            if cur_endpoint not in desc_endpoints: continue
            if partition.GetKey() in endpoint_partition_map[cur_endpoint]: continue
            if len(all_dict[cur_endpoint]) < min_partition_num:
                min_partition_num = len(all_dict[cur_endpoint])
                desc_endpoint = cur_endpoint
        if desc_endpoint == "":
            log.error(f"can not find endpoint to migrate. {db} {name} {record[1]} in {endpoint}")
            continue
        log.info(f"migrate table {partition.GetName()} partition {partition.GetPid()} in {db} from {endpoint} to {desc_endpoint}")
        status = MigratePartition(db, partition, endpoint, desc_endpoint, replica_map[partition.GetKey()] == 1)
        if not status.OK():
            log.error(status.GetMsg())
            log.error(f"migrate table {partition.GetName()} partition {partition.GetPid()} in {db} from {endpoint} to {desc_endpoint} failed")
            return status
        log.info(f"migrate table {partition.GetName()} partition {partition.GetPid()} in {db} from {endpoint} to {desc_endpoint} success")

    return Status()

def ScaleIn(executor : Executor, endpoints : list):
    status, result = executor.ShowTablet()
    if not status.OK():
        log.error(f"execute showtablet failed")
        return
    alive_endpoints = []
    for record in result:
        if record[2] == "kHealthy" : alive_endpoints.append(record[0])
    for endpoint in endpoints:
        if endpoint not in alive_endpoints:
            log.error(f"{endpoint} is not alive, cannot execute scale-in")
            return
        alive_endpoints.remove(endpoint)
    for endpoint in endpoints:
        if not ScaleInEndpoint(executor, endpoint, alive_endpoints).OK():
            return
    log.info("execute scale-in success")

if __name__ == "__main__":
    (options, args) = parser.parse_args()
    if options.cmd not in ["recoverdata", "scalein", "scaleout"]:
        log.error(f"unsupported cmd {options.cmd}")
        sys.exit()
    executor = Executor(options.openmldb_bin_path, options.zk_cluster, options.zk_root_path)
    if not executor.Connect().OK():
        log.error("connect OpenMLDB failed")
        sys.exit()
    status, auto_failover = executor.GetAutofailover()
    if not status.OK():
        log.error("get failover failed")
        sys.exit()
    if auto_failover and not executor.SetAutofailover("false").OK():
        log.error("set auto_failover failed")
        sys.exit()
    if options.cmd == "recoverdata":
        RecoverData(executor)
    elif options.cmd == "scaleout":
        ScaleOut(executor)
    elif options.cmd == "scalein":
        if options.endpoints is None or options.endpoints == '':
            log.error("no endpoint specified")
        else:
            endpoints = options.endpoints.split(",")
            if (len(endpoints) > 0):
                ScaleIn(executor, endpoints)
            else:
                log.error("no endpoint specified")
    if auto_failover and not executor.SetAutofailover("true").OK():
        log.warn("set auto_failover failed")