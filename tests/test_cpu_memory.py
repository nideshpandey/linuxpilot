from linuxpilot.checks.cpu import _usage, cpu_load, cpu_temperature, cpu_usage
from linuxpilot.checks.memory import memory_usage, oom_kills
from linuxpilot.models import Status

# ------------------------------------------------------------------ load


def test_load_ok(make_ctx):
    ctx = make_ctx({"/proc/loadavg": "0.50 0.80 0.70 1/200 1234\n"}, cores=4)
    r = cpu_load(ctx)
    assert r.status is Status.OK
    assert r.details["load5_per_core"] == 0.2


def test_load_warning_and_critical_scale_with_cores(make_ctx):
    assert cpu_load(make_ctx({"/proc/loadavg": "7 7 7 1/1 1"}, cores=4)).status is Status.WARNING
    assert cpu_load(make_ctx({"/proc/loadavg": "13 13 13 1/1 1"}, cores=4)).status is Status.CRITICAL
    # Same load on a 16-core machine is fine.
    assert cpu_load(make_ctx({"/proc/loadavg": "13 13 13 1/1 1"}, cores=16)).status is Status.OK


def test_load_missing_file_is_unknown(make_ctx):
    r = cpu_load(make_ctx())
    assert r.status is Status.UNKNOWN
    assert r.limitations


def test_load_malformed_is_unknown(make_ctx):
    assert cpu_load(make_ctx({"/proc/loadavg": "garbage"})).status is Status.UNKNOWN


# ------------------------------------------------------------------ usage


def test_usage_math():
    before = [100, 0, 100, 700, 50, 0, 0, 50, 0, 0]
    after = [200, 0, 200, 1300, 150, 0, 0, 150, 0, 0]  # delta total = 1000
    u = _usage(before, after)
    assert u["idle_percent"] == 60.0
    assert u["iowait_percent"] == 10.0
    assert u["steal_percent"] == 10.0
    assert u["busy_percent"] == 30.0


def test_usage_skipped_in_quick_mode(make_ctx):
    assert cpu_usage(make_ctx(quick=True)).status is Status.SKIPPED


def test_usage_reads_proc_stat(make_ctx):
    ctx = make_ctx({"/proc/stat": "cpu  100 0 100 700 0 0 0 0 0 0\ncpu0 1 1 1 1\n"})
    # Two identical samples -> no elapsed time -> honest UNKNOWN, not 0%.
    r = cpu_usage(ctx)
    assert r.status is Status.UNKNOWN


# ------------------------------------------------------------------ temperature


def test_temperature_unsupported_without_sensors(make_ctx):
    r = cpu_temperature(make_ctx())
    assert r.status is Status.UNSUPPORTED


def test_temperature_from_hwmon_coretemp(make_ctx):
    ctx = make_ctx({
        "/sys/class/hwmon/hwmon0/name": "coretemp\n",
        "/sys/class/hwmon/hwmon0/temp1_input": "52000\n",
        "/sys/class/hwmon/hwmon0/temp1_label": "Package id 0\n",
        "/sys/class/hwmon/hwmon1/name": "nvme\n",
        "/sys/class/hwmon/hwmon1/temp1_input": "61000\n",
    })
    r = cpu_temperature(ctx)
    assert r.status is Status.OK
    assert r.details["celsius"] == 52.0  # the NVMe drive is not the CPU
    assert r.details["sensor"] == "coretemp"


def test_temperature_raspberry_pi_zone_warning(make_ctx):
    ctx = make_ctx({
        "/sys/class/thermal/thermal_zone0/type": "cpu-thermal\n",
        "/sys/class/thermal/thermal_zone0/temp": "82500\n",
    })
    r = cpu_temperature(ctx)
    assert r.status is Status.WARNING
    assert r.recommendation


def test_temperature_critical(make_ctx):
    ctx = make_ctx({"/sys/class/thermal/thermal_zone0/type": "x86_pkg_temp",
                    "/sys/class/thermal/thermal_zone0/temp": "95000"})
    assert cpu_temperature(ctx).status is Status.CRITICAL


def test_temperature_ignores_implausible_values(make_ctx):
    ctx = make_ctx({"/sys/class/thermal/thermal_zone0/type": "cpu-thermal",
                    "/sys/class/thermal/thermal_zone0/temp": "-273000"})
    assert cpu_temperature(ctx).status is Status.UNSUPPORTED


def test_temperature_unlabelled_sensor_notes_uncertainty(make_ctx):
    ctx = make_ctx({"/sys/class/thermal/thermal_zone0/type": "acpitz",
                    "/sys/class/thermal/thermal_zone0/temp": "45000"})
    r = cpu_temperature(ctx)
    assert r.status is Status.OK
    assert r.limitations and "acpitz" in r.limitations[0]


# ------------------------------------------------------------------ memory

MEMINFO = """MemTotal:        8000000 kB
MemFree:          500000 kB
MemAvailable:    {avail} kB
Buffers:          100000 kB
Cached:          1000000 kB
SwapTotal:       {swap} kB
SwapFree:        {swap} kB
"""


def test_memory_ok(make_ctx):
    ctx = make_ctx({"/proc/meminfo": MEMINFO.format(avail=4000000, swap=2000000)})
    r = memory_usage(ctx)
    assert r.status is Status.OK
    assert r.details["available_percent"] == 50.0
    assert "swap" in r.summary


def test_memory_warning_and_critical(make_ctx):
    assert memory_usage(make_ctx({"/proc/meminfo": MEMINFO.format(avail=600000, swap=0)})).status is Status.WARNING
    r = memory_usage(make_ctx({"/proc/meminfo": MEMINFO.format(avail=200000, swap=0)}))
    assert r.status is Status.CRITICAL
    assert "no swap" in r.recommendation


def test_memory_pressure_raises_warning(make_ctx):
    ctx = make_ctx({
        "/proc/meminfo": MEMINFO.format(avail=4000000, swap=0),
        "/proc/pressure/memory": "some avg10=40.00 avg60=35.00 avg300=10.00 total=1\nfull avg10=1 avg60=1 avg300=1 total=1\n",
    })
    r = memory_usage(ctx)
    assert r.status is Status.WARNING
    assert "stalled" in r.reason


def test_memory_without_memavailable_estimates_and_says_so(make_ctx):
    ctx = make_ctx({"/proc/meminfo": "MemTotal: 1000 kB\nMemFree: 300 kB\nBuffers: 100 kB\nCached: 100 kB\n"})
    r = memory_usage(ctx)
    assert r.details["available_percent"] == 50.0
    assert r.limitations


def test_memory_unreadable_is_unknown(make_ctx):
    assert memory_usage(make_ctx()).status is Status.UNKNOWN


def test_oom_counter(make_ctx):
    assert oom_kills(make_ctx({"/proc/vmstat": "nr_free 1\noom_kill 0\n"})).status is Status.OK
    r = oom_kills(make_ctx({"/proc/vmstat": "nr_free 1\noom_kill 3\n"}))
    assert r.status is Status.WARNING and "3 processes" in r.summary
    assert oom_kills(make_ctx({"/proc/vmstat": "nr_free 1\n"})).status is Status.UNSUPPORTED
