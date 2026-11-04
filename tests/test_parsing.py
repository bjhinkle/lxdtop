"""Reading the kernel's counters: each parser run on fixture files in the kernel's formats."""

import os
import tempfile
import unittest

from tests.support import FakeHost, lxdtop


class ReadTest(unittest.TestCase):
    """read() and read_int() on real (temporary) files."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def write(self, name, data):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(data.encode() if isinstance(data, str) else data)
        return path

    def test_read_returns_the_text(self):
        self.assertEqual(lxdtop.read(self.write("a", "123\n")), "123\n")

    def test_read_returns_none_for_missing_files_and_no_path(self):
        self.assertIsNone(lxdtop.read(os.path.join(self.dir, "missing")))
        self.assertIsNone(lxdtop.read(None))
        self.assertIsNone(lxdtop.read(""))

    def test_read_returns_none_for_a_directory(self):
        self.assertIsNone(lxdtop.read(self.dir))

    def test_read_int_takes_the_first_number(self):
        self.assertEqual(lxdtop.read_int(self.write("n", "536870912\n")), 536870912)
        self.assertEqual(lxdtop.read_int(self.write("two", "42 17\n")), 42)

    def test_read_int_is_none_for_max_empty_or_missing(self):
        self.assertIsNone(lxdtop.read_int(self.write("max", "max\n")))       # memory.max with no limit
        self.assertIsNone(lxdtop.read_int(self.write("empty", "")))
        self.assertIsNone(lxdtop.read_int(os.path.join(self.dir, "missing")))
        self.assertIsNone(lxdtop.read_int(None))


class CgroupFileTest(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost(self)
        self.host.copy_fixture("cgroup-cpu.stat", "/cg/cpu.stat")
        self.host.copy_fixture("cgroup-memory.stat", "/cg/memory.stat")
        self.host.copy_fixture("cgroup-io.stat", "/cg/io.stat")

    def test_read_fields_picks_the_wanted_names(self):
        with self.host.installed():
            self.assertEqual(lxdtop.read_fields("/cg/cpu.stat", ("usage_usec",)), {"usage_usec": 1234567890})
            self.assertEqual(lxdtop.read_fields("/cg/memory.stat", ("inactive_file", "anon")),
                             {"inactive_file": 268435456, "anon": 268435456})

    def test_read_fields_matches_whole_names_only(self):
        # "file" must not pick up "inactive_file" or "file_mapped".
        with self.host.installed():
            self.assertEqual(lxdtop.read_fields("/cg/memory.stat", ("file",)), {"file": 402653184})

    def test_read_fields_of_a_missing_file_is_empty(self):
        with self.host.installed():
            self.assertEqual(lxdtop.read_fields("/cg/nothing", ("usage_usec",)), {})

    def test_read_io_stat_adds_up_every_device(self):
        with self.host.installed():
            self.assertEqual(lxdtop.read_io_stat("/cg/io.stat"), (1048576 + 4194304, 2097152))

    def test_read_io_stat_of_an_empty_or_missing_file(self):
        self.host.write("/cg/io.empty", "")
        with self.host.installed():
            self.assertEqual(lxdtop.read_io_stat("/cg/io.empty"), (0, 0))
            self.assertIsNone(lxdtop.read_io_stat("/cg/io.missing"))


class HostCountersTest(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost(self)

    def test_read_cpu_times_whole_host_then_each_thread(self):
        self.host.copy_fixture("proc-stat.txt", "/proc/stat")
        with self.host.installed():
            whole, threads = lxdtop.read_cpu_times()
        # busy = user + nice + system + irq + softirq + steal; idle = idle + iowait
        self.assertEqual(whole, (4705 + 356 + 584 + 277, 3728158))
        self.assertEqual(threads, [(1791, 932092), (1378, 931974), (1395, 932040), (1358, 932052)])

    def test_read_cpu_times_without_proc_stat(self):
        with self.host.installed():
            self.assertEqual(lxdtop.read_cpu_times(), ((0, 0), []))

    def test_read_meminfo_in_bytes(self):
        self.host.copy_fixture("proc-meminfo.txt", "/proc/meminfo")
        with self.host.installed():
            info = lxdtop.read_meminfo()
        self.assertEqual(info, dict(MemTotal=65536000 * 1024, MemAvailable=32768000 * 1024,
                                    SwapTotal=8388604 * 1024, SwapFree=8000000 * 1024))

    def test_read_meminfo_missing_is_all_zero(self):
        with self.host.installed():
            self.assertEqual(set(lxdtop.read_meminfo().values()), {0})

    def test_arc_size_from_arcstats(self):
        self.host.copy_fixture("zfs-arcstats.txt", lxdtop.ZFS_ARCSTATS)
        with self.host.installed():
            # "size" only: not c, compressed_size or uncompressed_size.
            self.assertEqual(lxdtop.read_kstat(lxdtop.ZFS_ARCSTATS, ("size",)), {"size": "8589934592"})

    def test_read_kstat_missing_file(self):
        with self.host.installed():
            self.assertIsNone(lxdtop.read_kstat("/proc/spl/kstat/zfs/arcstats", ("size",)))

    def test_cpu_temperature_sensor(self):
        self.host.write("/sys/class/hwmon/hwmon0/name", "acpitz\n")
        self.host.write("/sys/class/hwmon/hwmon0/temp1_input", "27800\n")
        self.host.write("/sys/class/hwmon/hwmon1/name", "nvme\n")
        self.host.write("/sys/class/hwmon/hwmon2/name", "k10temp\n")
        self.host.write("/sys/class/hwmon/hwmon2/temp1_input", "54250\n")
        with self.host.installed():
            self.assertEqual(lxdtop.find_cpu_temp_sensor(), "/sys/class/hwmon/hwmon2/temp1_input")

    def test_intel_coretemp_and_no_sensor(self):
        with self.host.installed():
            self.assertIsNone(lxdtop.find_cpu_temp_sensor())
        self.host.write("/sys/class/hwmon/hwmon3/name", "coretemp\n")
        with self.host.installed():
            self.assertEqual(lxdtop.find_cpu_temp_sensor(), "/sys/class/hwmon/hwmon3/temp1_input")

    def test_physical_nics_are_hardware_ports_that_are_up(self):
        self.host.add_nic("eno1", 1, 1, physical=True)
        self.host.add_nic("enp5s0", 1, 1, physical=True)
        self.host.add_nic("eno2", 1, 1, physical=True, state="down")
        self.host.add_nic("lxdbr0", 1, 1)                  # a bridge: no device
        self.host.add_nic("veth1a2b3c4d", 1, 1)            # a container's cable
        with self.host.installed():
            self.assertEqual(lxdtop.physical_nics(), ["eno1", "enp5s0"])


class InstanceCountersTest(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost(self)

    def test_container_of_reads_the_cgroup_v2_line(self):
        self.host.write("/proc/10/cgroup", "0::/lxc.payload.web/system.slice/nginx.service\n")
        self.host.write("/proc/11/cgroup", "0::/lxc.payload.dev_api/init.scope\n")
        self.host.write("/proc/12/cgroup", "0::/user.slice/user-1000.slice/session-3.scope\n")
        self.host.write("/proc/13/cgroup", "0::/lxc.monitor.web\n")       # LXD's monitor runs on the host
        with self.host.installed():
            self.assertEqual(lxdtop.container_of(10), "web")
            self.assertEqual(lxdtop.container_of(11), "dev_api")
            self.assertIsNone(lxdtop.container_of(12))
            self.assertIsNone(lxdtop.container_of(13))
            self.assertIsNone(lxdtop.container_of(14))                    # gone

    def test_container_of_on_a_hybrid_cgroup_host(self):
        self.host.write("/proc/10/cgroup", "12:pids:/lxc.payload.web\n1:name=systemd:/lxc.payload.web\n"
                                           "0::/lxc.payload.web\n")
        with self.host.installed():
            self.assertEqual(lxdtop.container_of(10), "web")

    def test_container_of_uses_only_the_first_path_part(self):
        # A container's root can name its own sub-cgroups; only the first part is LXD's.
        self.host.write("/proc/10/cgroup", "0::/lxc.payload.web/lxc.payload.other/x\n")
        with self.host.installed():
            self.assertEqual(lxdtop.container_of(10), "web")

    def test_read_vm_process(self):
        self.host.copy_fixture("pid-stat-qemu.txt", "/proc/5151/stat")
        self.host.copy_fixture("pid-status-qemu.txt", "/proc/5151/status")
        with self.host.installed():
            cpu_usec, rss, start = lxdtop.read_vm_process(5151)
        self.assertEqual(cpu_usec, (360000 + 90000) * 1_000_000 // lxdtop.CLOCK_TICKS)
        self.assertEqual(rss, 2097152 * 1024)
        self.assertEqual(start, 1234500)

    def test_read_vm_process_rejects_other_processes_and_no_pid(self):
        self.host.add_process(5151, "bash")                # the PID now belongs to something else
        with self.host.installed():
            self.assertIsNone(lxdtop.read_vm_process(5151))
            self.assertIsNone(lxdtop.read_vm_process(5152))    # gone
            self.assertIsNone(lxdtop.read_vm_process(0))       # stopped VMs report pid 0
            self.assertIsNone(lxdtop.read_vm_process(None))

    def test_read_net_swaps_in_and_out_and_adds_interfaces(self):
        self.host.add_nic("veth22220000", rx=100, tx=2000)
        self.host.add_nic("veth22221111", rx=50, tx=500)
        with self.host.installed():
            # The host end receives what the instance sends, so in/out swap.
            self.assertEqual(lxdtop.read_net(["veth22220000", "veth22221111"]), (2500, 150))
            self.assertEqual(lxdtop.read_net(["veth22220000", "gone0"]), (2000, 100))
            self.assertIsNone(lxdtop.read_net([]))
            self.assertIsNone(lxdtop.read_net(["gone0"]))

    def test_a_name_with_a_nul_is_no_reading(self):
        with self.host.installed():
            self.assertIsNone(lxdtop.read_net(["veth\x00x"]))

    def test_interface_names_stay_inside_sys_class_net(self):
        """SECURITY-REVIEW.md L-7: a name with "/" in it used to read outside /sys/class/net.

        read_net() put the host_name LXD reports into the path unchecked, so
        "../../../srv/x" read /srv/x/statistics/rx_bytes, and a FIFO there
        would have blocked the sampling loop. No interface name contains "/".
        """
        self.host.add_nic("../../../srv/x", rx=111, tx=222)
        self.host.add_nic("veth22220000", rx=100, tx=2000)
        with self.host.installed():
            self.assertIsNone(lxdtop.read_net(["../../../srv/x"]))
            self.assertEqual(lxdtop.read_net(["../../../srv/x", "veth22220000"]), (2000, 100))
            self.assertIsNone(lxdtop.read_net([5]))              # not text: no reading, and no error


class ZfsDatasetTest(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost(self)

    def test_maps_container_and_vm_datasets(self):
        self.host.copy_fixture("zfs-objset-container.txt", "/proc/spl/kstat/zfs/default/objset-0x36")
        self.host.copy_fixture("zfs-objset-vm.txt", "/proc/spl/kstat/zfs/default/objset-0x52")
        self.host.add_objset("default", "0x40", "default/containers/dev_api", 1, 1)   # project "dev"
        self.host.add_objset("tank", "0x10", "tank/lxd/containers/db", 1, 1)          # pool on a sub-dataset
        with self.host.installed():
            mapping = lxdtop.find_zfs_datasets()
        self.assertEqual(mapping, {
            "web": "/proc/spl/kstat/zfs/default/objset-0x36",
            "win": "/proc/spl/kstat/zfs/default/objset-0x52",
            "dev_api": "/proc/spl/kstat/zfs/default/objset-0x40",
            "db": "/proc/spl/kstat/zfs/tank/objset-0x10",
        })

    def test_skips_snapshots_images_and_child_datasets(self):
        self.host.add_objset("default", "0x1", "default/containers/web@snap0", 1, 1)
        self.host.add_objset("default", "0x2", "default/images/6d1b2c3a", 1, 1)
        self.host.add_objset("default", "0x3", "default/containers/web/data", 1, 1)
        self.host.add_objset("default", "0x4", "default/virtual-machines/win", 1, 1)   # the VM's config fs
        self.host.add_objset("default", "0x5", "default", 1, 1)
        with self.host.installed():
            self.assertEqual(lxdtop.find_zfs_datasets(), {})

    def test_reads_disk_counters(self):
        self.host.copy_fixture("zfs-objset-container.txt", "/proc/spl/kstat/zfs/default/objset-0x36")
        with self.host.installed():
            stats = lxdtop.read_kstat("/proc/spl/kstat/zfs/default/objset-0x36", ("nread", "nwritten"))
        self.assertEqual(stats, {"nread": "188436480", "nwritten": "286476288"})

    def test_a_delegated_child_dataset_cannot_claim_another_instance(self):
        """SECURITY-REVIEW.md L-8: a nested dataset used to take over another instance's counters.

        With zfs.delegate, a container's root can create datasets under its own,
        such as default/containers/c1/containers/victim. find_zfs_datasets()
        took the part after the last "/containers/", so that dataset was mapped
        to "victim", and whichever kstat file came last won. An instance's own
        dataset always has the shorter name, so the shorter one now wins.
        """
        for victim_id, nested_id in (("0x10", "0x20"), ("0x30", "0x05")):    # either one read first
            with self.subTest(victim=victim_id, nested=nested_id):
                host = FakeHost(self)
                host.add_objset("default", victim_id, "default/containers/victim", 100, 100)
                host.add_objset("default", nested_id, "default/containers/c1/containers/victim", 999, 999)
                host.add_objset("default", "0x40", "default/virtual-machines/vm1.block", 1, 1)
                host.add_objset("default", "0x41", "default/containers/c1/virtual-machines/vm1.block", 9, 9)
                with host.installed():
                    mapping = lxdtop.find_zfs_datasets()
                self.assertEqual(mapping["victim"], f"/proc/spl/kstat/zfs/default/objset-{victim_id}")
                self.assertEqual(mapping["vm1"], "/proc/spl/kstat/zfs/default/objset-0x40")

    @unittest.expectedFailure
    def test_a_nested_dataset_cannot_claim_an_instance_without_zfs(self):
        """SECURITY-REVIEW.md L-8, the part not fixed.

        When "victim" has no ZFS dataset of its own (its disk is on a dir or
        LVM pool, say), there is no shorter name to prefer, so c1's nested
        dataset is still mapped to it. Telling them apart needs each pool's own
        dataset, from LXD's storage-pool config.
        """
        self.host.add_objset("default", "0x20", "default/containers/c1/containers/victim", 999, 999)
        with self.host.installed():
            self.assertNotIn("victim", lxdtop.find_zfs_datasets())

if __name__ == "__main__":
    unittest.main()
