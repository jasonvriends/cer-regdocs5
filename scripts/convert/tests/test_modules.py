"""Every name the stages' functions use is defined: a function moved between modules
without its imports fails only when a worker reaches it (release_memory, 2026-10-10).

    scripts/convert/.venv/bin/python -m unittest discover -s scripts/convert/tests
"""
import builtins
import dis
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def undefined(modname):
    m = __import__(modname)
    out = set()

    def walk(code, where):
        for ins in dis.get_instructions(code):
            if ins.opname in ("LOAD_GLOBAL", "LOAD_NAME") and ins.argval not in vars(m) \
                    and not hasattr(builtins, ins.argval):
                out.add(f"{where}: {ins.argval}")
        for k in code.co_consts:
            if isinstance(k, types.CodeType):
                walk(k, where)

    for name, obj in vars(m).items():
        if isinstance(obj, types.FunctionType) and obj.__module__ == modname:
            walk(obj.__code__, name)
        elif isinstance(obj, type) and obj.__module__ == modname:
            for v in vars(obj).values():
                if isinstance(v, types.FunctionType):
                    walk(v.__code__, f"{name}.{v.__name__}")
    return sorted(out)


class Names(unittest.TestCase):
    def test_every_global_a_stage_uses_is_defined(self):
        for mod in ("common", "pagelib", "tier0", "pages", "convert"):
            with self.subTest(module=mod):
                self.assertEqual(undefined(mod), [])

    def test_each_stage_hashes_the_code_it_runs(self):
        import convert
        import pages
        for stage in (convert.Convert, pages.Pages):
            with self.subTest(stage=stage.__name__):
                self.assertIn("pagelib.py", [p.name for p in stage.code_files])


if __name__ == "__main__":
    unittest.main()
