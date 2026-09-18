import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bench import parity

REPO_FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"


def _fw(root):
    """A minimal well-formed firmware tree."""
    common = root / "firmware" / "bench_common"
    (common / "include").mkdir(parents=True)
    for n in parity.CONTRACT_NAMES:
        d = common / ("include" if n.endswith(".h") else ".")
        d.mkdir(parents=True, exist_ok=True)
        (d / n).write_text(f"/* {n} */\n")
    (common / "include" / "control.h").write_text(
        "#define CONTROL_PROTOCOL_VERSION 1\n")
    for t in parity.TARGETS:
        m = root / "firmware" / t / "main"
        m.mkdir(parents=True)
        (root / "firmware" / t / "CMakeLists.txt").write_text(
            'set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../bench_common")\n')
        (m / "app_main.c").write_text("void app_main(void){}\n")
    (root / "firmware" / ".idf-version").write_text("6.0.2\n")
    return root / "firmware"


def test_the_real_repo_passes():
    assert parity.check(REPO_FW)["ok"], parity.summary(parity.check(REPO_FW))


def test_clean_tree_passes(tmp_path):
    assert parity.check(_fw(tmp_path))["ok"]


def test_a_target_that_forks_the_core_fails(tmp_path):
    """The monorepo failure mode: copy pipe.c into a target to 'just tweak it'."""
    fw = _fw(tmp_path)
    (fw / "esp32s3" / "main" / "pipe.c").write_text("/* tweaked for the S3 */\n")
    res = parity.check(fw)
    assert not res["ok"]
    assert any("shadows a contract file" in p for p in res["problems"])


def test_a_target_that_stops_using_the_shared_core_fails(tmp_path):
    fw = _fw(tmp_path)
    (fw / "esp32c5" / "CMakeLists.txt").write_text("project(solo)\n")
    res = parity.check(fw)
    assert not res["ok"]
    assert any("not using the shared measurement core" in p for p in res["problems"])


def test_duplicate_protocol_definition_fails(tmp_path):
    fw = _fw(tmp_path)
    (fw / "esp32s3" / "main" / "myproto.h").write_text(
        "#define CONTROL_PROTOCOL_VERSION 2\n")
    res = parity.check(fw)
    assert not res["ok"]
    assert any("defined in 2 files" in p for p in res["problems"])


def test_missing_idf_pin_fails(tmp_path):
    fw = _fw(tmp_path)
    (fw / ".idf-version").unlink()
    assert not parity.check(fw)["ok"]


def test_sdkconfig_divergence_is_NOT_checked(tmp_path):
    """Divergent configuration is the experiment. Only the contract is locked."""
    fw = _fw(tmp_path)
    (fw / "esp32s3" / "sdkconfig.defaults").write_text("CONFIG_X=y\n")
    (fw / "esp32c5" / "sdkconfig.defaults").write_text("CONFIG_X=n\nCONFIG_Y=y\n")
    assert parity.check(fw)["ok"]
