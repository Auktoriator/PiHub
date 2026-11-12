"""Run with: python3 -m unittest discover -s tests"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pihub import cards  # noqa: E402

AREAS = '[[areas]]\nid = "net"\nname = "Network"\n'
CARD = '''id = "web"
name = "Web server"
area = "net"
summary = "Hands out pages"
about = "Takes web requests."
units = ["web.timer"]
[[ports]]
port = 443
reach = "lan"
why = "The site."
'''


def unit(own=True):
    return {"active": "active", "own": own}


def listen(port, bound):
    return {"proto": "tcp", "port": port, "bound": bound, "processes": ["x"]}


class Cards(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "cards").mkdir()
        (self.dir / "areas.toml").write_text(AREAS)
        self.addCleanup(shutil.rmtree, self.dir)

    def write(self, name, text):
        (self.dir / "cards" / name).write_text(text)

    def keys(self):
        return [key for _, key, _ in cards.load(self.dir)[2]]

    def test_examples_are_valid(self):
        self.assertEqual(cards.load(ROOT / "examples")[2], [])

    def test_good_card_has_no_problems(self):
        self.write("web.toml", CARD)
        self.assertEqual(self.keys(), [])

    def test_problems_are_named(self):
        self.write("web.toml", CARD.replace('area = "net"', 'area = "nowhere"') + '''
[[actions]]
label = "Stop"
run = "systemctl stop web"
level = "change"
''')
        self.write("other.toml", 'needs = ["ghost"]\ncolour = 1\n' + CARD.replace('reach = "lan"', 'reach = "moon"'))
        self.assertCountEqual(self.keys(), ["p_area", "p_effect", "p_id", "p_reach", "p_need", "p_unknown"])

    def action_keys(self, extra, run="echo hi"):
        self.write("web.toml", CARD + f'''
[[actions]]
label = "Do"
run = "{run}"
level = "read"
{extra}
''')
        return self.keys()

    def test_action_timeout(self):
        self.assertEqual(self.action_keys("timeout = 1"), [])
        self.assertEqual(self.action_keys("timeout = 3600"), [])
        for bad in ("0", "3601", "-5", "1.5", '"60"', "true"):
            self.assertEqual(self.action_keys(f"timeout = {bad}"), ["p_timeout"], bad)

    def test_action_choose(self):
        for source in cards.CHOICE_SOURCES:
            self.assertEqual(self.action_keys(f'choose = "{source}"', "echo {value}"), [])
        self.assertEqual(self.action_keys('choose = "everything"', "echo {value}"), ["p_choose"])

    def test_action_value_placeholder(self):
        self.assertEqual(self.action_keys('choose = "blocked_addresses"', "echo hi"), ["p_value"])
        self.assertEqual(self.action_keys('choose = "blocked_addresses"', "echo {value} {value}"), ["p_value"])
        self.assertEqual(self.action_keys("", "echo {value}"), ["p_value"])

    def test_new_problem_texts_exist_in_both_languages(self):
        from pihub.texts import TEXTS
        for lang in ("en", "de"):
            for key in ("p_timeout", "p_choose", "p_value"):
                self.assertIn(key, TEXTS[lang])
        self.assertIn("{value}", TEXTS["en"]["p_value"].format(n=1))
        self.assertIn("{value}", TEXTS["de"]["p_value"].format(n=1))

    def test_coverage(self):
        self.write("web.toml", CARD)
        _, loaded, _ = cards.load(self.dir)
        units = {"web.service": unit(), "web.timer": unit(), "stray.service": unit(), "vendor.service": unit(False),
                 "retired.service": {"active": "inactive", "enabled": "disabled", "own": True}}
        cov = cards.coverage((units, [{"name": "box", "state": "running"}],
                              [listen(443, "local"), listen(8000, "all"), listen(40000, "all")]), loaded)
        self.assertEqual([(k, n) for k, n, _ in cov["unclaimed"]],
                         [("unit", "stray.service"), ("container", "box"), ("port", "8000/tcp")])
        self.assertEqual(cov["high_ports"], 1)
        self.assertEqual([key for key, _ in cov["notes"]], ["n_only_local"])

    def test_port_meant_local_but_bound_wide(self):
        self.write("web.toml", CARD.replace('reach = "lan"', 'reach = "local"'))
        _, loaded, _ = cards.load(self.dir)
        cov = cards.coverage(({}, [], [listen(443, "all")]), loaded)
        self.assertEqual([key for key, _ in cov["notes"]], ["n_wider"])


if __name__ == "__main__":
    unittest.main()
