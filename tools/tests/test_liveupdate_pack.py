"""Tests for tools/liveupdate_pack.py.

The packer runs as a subprocess on a small synthetic bob output
(game.graph.json + liveupdate_dist/), so no Defold build is needed:

    python3 -m unittest discover -s tools/tests
"""

import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS_DIR)

import liveupdate_ddf_pb2  # noqa: E402

PACKER = os.path.join(TOOLS_DIR, "liveupdate_pack.py")

# path -> (content, children). The two levels own the same background under
# different paths; level1 also has two identical fonts (dedup inside one archive).
RESOURCES = {
    "/levels/level1.collectionc": (
        b"level1",
        ["/levels/level1.goc", "/level1/bg.texturec", "/level1/a.fontc", "/level1/b.fontc"],
    ),
    "/levels/level1.goc": (b"level1 go", []),
    "/level1/bg.texturec": (b"background", []),
    "/level1/a.fontc": (b"font", []),
    "/level1/b.fontc": (b"font", []),
    "/levels/level2.collectionc": (b"level2", ["/levels/level2.goc", "/level2/bg.texturec"]),
    "/levels/level2.goc": (b"level2 go", []),
    "/level2/bg.texturec": (b"background", []),
}


def write_fixture(root):
    dist = os.path.join(root, "liveupdate_dist")
    os.makedirs(dist)
    os.makedirs(os.path.join(root, "build", "default"))

    data = liveupdate_ddf_pb2.ManifestData()
    data.header.resource_hash_algorithm = liveupdate_ddf_pb2.HASH_SHA1
    data.header.signature_hash_algorithm = liveupdate_ddf_pb2.HASH_SHA1
    data.header.signature_sign_algorithm = liveupdate_ddf_pb2.SIGN_RSA
    data.header.project_identifier.data = b"\0" * 20
    graph = [
        {
            "path": f"/main/main.collectionc#{name}_proxy",
            "children": [f"/levels/{name}.collectionc"],
            "isInMainBundle": True,
            "nodeType": "ExcludedCollectionProxy",
        }
        for name in ("level1", "level2")
    ]
    for path, (content, children) in RESOURCES.items():
        digest = hashlib.sha1(content).hexdigest()
        with open(os.path.join(dist, digest), "wb") as f:
            f.write(content)
        entry = data.resources.add(url=path, url_hash=len(graph), size=len(content))
        entry.hash.data = bytes.fromhex(digest)
        entry.compressed_size = 0xFFFFFFFF
        entry.flags = liveupdate_ddf_pb2.EXCLUDED
        graph.append(
            {"path": path, "children": children, "isInMainBundle": False, "hexDigest": digest}
        )

    with open(os.path.join(root, "build", "default", "game.graph.json"), "w") as f:
        json.dump(graph, f)
    manifest = liveupdate_ddf_pb2.ManifestFile(
        data=data.SerializeToString(), signature=b"signature", version=5
    )
    with open(os.path.join(dist, "liveupdate.game.dmanifest"), "wb") as f:
        f.write(manifest.SerializeToString())


def run_packer(root, *args):
    env = {k: v for k, v in os.environ.items() if k != "LIVEUPDATE_COLLECTION_SUFFIX"}
    subprocess.run(
        [sys.executable, PACKER, *args], cwd=root, env=env, check=True, stdout=subprocess.DEVNULL
    )


def check_archives(test, out_dir, files_tree):
    """Every listed resource is in its archive, and each archive stores it once."""
    for name, archive in files_tree["zip_files"].items():
        # Exact "<name>_<16 hex>.arcd0": "name_*" alone also matches "name_texture_*".
        arcs = [
            a
            for a in glob.glob(os.path.join(out_dir, glob.escape(name) + "_*.arcd0"))
            if re.fullmatch(re.escape(name) + r"_[0-9a-f]{16}\.arcd0", os.path.basename(a))
        ]
        test.assertEqual(len(arcs), 1, name)
        names = zipfile.ZipFile(arcs[0]).namelist()
        test.assertEqual(len(names), len(set(names)), name)
        for f in archive["files"]:
            test.assertIn(f["hexDigest"], names, f"{f['path']} missing in {name}")


class PerArchiveDedupTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.out = os.path.join(self.root, "liveupdate_zip")
        write_fixture(self.root)
        run_packer(self.root)
        with open(os.path.join(self.out, "files_tree.json")) as f:
            self.files_tree = json.load(f)

    def test_pack(self):
        check_archives(self, self.out, self.files_tree)

    def test_restore_from_tree(self):
        # The low-res pass repacks from the files_tree.json left in the cwd.
        for path in glob.glob(os.path.join(self.out, "*")):
            os.remove(path)
        run_packer(self.root, "--restore_from_tree")
        check_archives(self, self.out, self.files_tree)


if __name__ == "__main__":
    unittest.main()
