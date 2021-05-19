# Copyright (C) 2017 FireEye, Inc. All Rights Reserved.

import os

import yaml
import pytest
import viv_utils

import floss.main as floss_main
import floss.stackstrings as stackstrings
import floss.identification_manager as im


def extract_strings(vw):
    """
    Deobfuscate strings from vivisect workspace
    """
    decoding_functions_candidates = identify_decoding_functions(vw)
    decoded_strings = floss_main.decode_strings(vw, decoding_functions_candidates, 4)
    selected_functions = floss_main.select_functions(vw, None)
    decoded_stackstrings = stackstrings.extract_stackstrings(vw, selected_functions, 4)
    decoded_strings.extend(decoded_stackstrings)
    return [ds.s for ds in decoded_strings]


def identify_decoding_functions(vw):
    selected_functions = floss_main.select_functions(vw, None)
    selected_plugin_names = floss_main.select_plugins(None)
    selected_plugins = [p for p in floss_main.get_all_plugins() if str(p) in selected_plugin_names]
    decoding_functions_candidates = im.identify_decoding_functions(vw, selected_plugins, selected_functions)
    return decoding_functions_candidates


def pytest_collect_file(parent, path):
    if path.basename == "test.yml":
        return YamlFile.from_parent(parent, fspath=path)


class YamlFile(pytest.File):
    def collect(self):
        spec = yaml.safe_load(self.fspath.open())
        test_dir = os.path.dirname(str(self.fspath))
        for platform, archs in list(spec["Output Files"].items()):
            for arch, filename in list(archs.items()):
                # TODO specify max runtime via command line option
                MAX_RUNTIME = 30.0
                try:
                    runtime_raw = spec["FLOSS running time"]
                    runtime = float(runtime_raw.split(" ")[0])
                    if runtime > MAX_RUNTIME:
                        # skip this test
                        continue
                except KeyError:
                    pass
                except ValueError:
                    pass
                filepath = os.path.join(test_dir, filename)
                if os.path.exists(filepath):
                    yield FLOSSTest.from_parent(self, path=filepath, platform=platform, arch=arch, filename=filename, spec=spec)


class FLOSSTestError(Exception):
    def __init__(self, expected, got):
        self.expected = expected
        self.got = got


class FLOSSStringsNotExtracted(FLOSSTestError):
    pass


class FLOSSDecodingFunctionNotFound(Exception):
    pass


class FLOSSTest(pytest.Item):
    def __init__(self, parent, path, platform, arch, filename, spec):
        name = "{name:s}::{platform:s}::{arch:s}".format(name=spec["Test Name"], platform=platform, arch=arch)
        super(FLOSSTest, self).__init__(name, parent)
        self.spec = spec
        self.platform = platform
        self.arch = arch
        self.filename = filename

    def _test_strings(self, test_path):
        expected_strings = set(self.spec["Decoded strings"])
        if not expected_strings:
            return

        test_shellcode = self.spec.get("Test shellcode")
        if test_shellcode:
            with open(test_path, "rb") as f:
                shellcode_data = f.read()
            vw = viv_utils.getShellcodeWorkspace(shellcode_data)  # TODO provide arch from test.yml
            found_strings = set(extract_strings(vw))
        else:
            vw = viv_utils.getWorkspace(test_path)
            found_strings = set(extract_strings(vw))

        if not (expected_strings <= found_strings):
            raise FLOSSStringsNotExtracted(expected_strings, found_strings)

    def _test_detection(self, test_path):
        try:
            expected_functions = set(self.spec["Decoding routines"][self.platform][self.arch])
        except KeyError:
            expected_functions = set([])

        if not expected_functions:
            return

        vw = viv_utils.getWorkspace(test_path)
        fs = [p[0] for p in identify_decoding_functions(vw).get_top_candidate_functions()]
        found_functions = set(fs)

        if not (expected_functions <= found_functions):
            raise FLOSSDecodingFunctionNotFound(expected_functions, found_functions)

    def runtest(self):
        xfail = self.spec.get("Xfail", {})
        if "all" in xfail:
            pytest.xfail("unsupported test case (known issue)")

        if "{0.platform:s}-{0.arch:s}".format(self) in xfail:
            pytest.xfail("unsupported platform&arch test case (known issue)")

        spec_path = self.location[0]
        test_dir = os.path.dirname(spec_path)
        test_path = os.path.join(test_dir, self.filename)

        self._test_detection(test_path)
        self._test_strings(test_path)

    def reportinfo(self):
        return self.fspath, 0, "usecase: %s" % self.name

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, FLOSSStringsNotExtracted):
            expected = excinfo.value.expected
            got = excinfo.value.got
            return "\n".join(
                [
                    "FLOSS extraction failed:",
                    "   expected: %s" % str(expected),
                    "   got: %s" % str(got),
                    "   expected-got: %s" % str(set(expected) - set(got)),
                    "   got-expected: %s" % str(set(got) - set(expected)),
                ]
            )
