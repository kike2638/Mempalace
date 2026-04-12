import os
import tempfile
import shutil
import json
import chromadb
from mempalace.convo_miner import mine_convos


def test_convo_mining():
    tmpdir = tempfile.mkdtemp()
    with open(os.path.join(tmpdir, "chat.txt"), "w") as f:
        f.write(
            "> What is memory?\nMemory is persistence.\n\n> Why does it matter?\nIt enables continuity.\n\n> How do we build it?\nWith structured storage.\n"
        )

    palace_path = os.path.join(tmpdir, "palace")
    mine_convos(tmpdir, palace_path, wing="test_convos")

    client = chromadb.PersistentClient(path=palace_path)
    col = client.get_collection("mempalace_drawers")
    assert col.count() >= 2

    # Verify search works
    results = col.query(query_texts=["memory persistence"], n_results=1)
    assert len(results["documents"][0]) > 0

    shutil.rmtree(tmpdir, ignore_errors=True)


def test_convo_mining_warns_on_ambiguous_chatgpt_branches(capsys):
    tmpdir = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmpdir, "ambiguous.json"), "w") as f:
            json.dump(
                {
                    "mapping": {
                        "root": {"parent": None, "message": None, "children": ["u1"]},
                        "u1": {
                            "parent": "root",
                            "message": {
                                "author": {"role": "user"},
                                "content": {"parts": ["Tell me a slogan"]},
                            },
                            "children": ["a_old", "a_new"],
                        },
                        "a_old": {
                            "parent": "u1",
                            "message": {
                                "author": {"role": "assistant"},
                                "content": {"parts": ["Old answer"]},
                            },
                            "children": [],
                        },
                        "a_new": {
                            "parent": "u1",
                            "message": {
                                "author": {"role": "assistant"},
                                "content": {"parts": ["New regenerated answer"]},
                            },
                            "children": [],
                        },
                    }
                },
                f,
            )

        palace_path = os.path.join(tmpdir, "palace")
        mine_convos(tmpdir, palace_path, wing="test_convos", dry_run=True)

        output = capsys.readouterr().out
        assert "multiple candidate conversation branches without current_node" in output
        assert "ambiguous.json" in output
    finally:
        shutil.rmtree(tmpdir)


def test_convo_mining_warns_on_invalid_chatgpt_export(capsys):
    tmpdir = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmpdir, "invalid.json"), "w") as f:
            json.dump(
                {
                    "current_node": "missing",
                    "mapping": {
                        "root": {"parent": None, "message": None, "children": ["u1"]},
                        "u1": {
                            "parent": "root",
                            "message": {
                                "author": {"role": "user"},
                                "content": {"parts": ["Tell me a slogan"]},
                            },
                            "children": ["a1"],
                        },
                        "a1": {
                            "parent": "u1",
                            "message": {
                                "author": {"role": "assistant"},
                                "content": {"parts": ["A better answer"]},
                            },
                            "children": [],
                        },
                    },
                },
                f,
            )

        palace_path = os.path.join(tmpdir, "palace")
        mine_convos(tmpdir, palace_path, wing="test_convos", dry_run=True)

        output = capsys.readouterr().out
        assert "current_node does not reference a mapping node" in output
        assert "invalid.json" in output
    finally:
        shutil.rmtree(tmpdir)


def test_convo_mining_reports_invalid_chatgpt_skip_summary(capsys):
    tmpdir = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmpdir, "invalid.json"), "w") as f:
            json.dump(
                {
                    "current_node": "missing",
                    "mapping": {
                        "root": {"parent": None, "message": None, "children": ["u1"]},
                        "u1": {
                            "parent": "root",
                            "message": {
                                "author": {"role": "user"},
                                "content": {"parts": ["Tell me a slogan"]},
                            },
                            "children": ["a1"],
                        },
                        "a1": {
                            "parent": "u1",
                            "message": {
                                "author": {"role": "assistant"},
                                "content": {"parts": ["A better answer"]},
                            },
                            "children": [],
                        },
                    },
                },
                f,
            )
        with open(os.path.join(tmpdir, "chat.txt"), "w") as f:
            f.write(
                "> What is memory?\nMemory is persistence.\n\n> Why does it matter?\nIt enables continuity.\n\n> How do we build it?\nWith structured storage.\n"
            )

        palace_path = os.path.join(tmpdir, "palace")
        mine_convos(tmpdir, palace_path, wing="test_convos", dry_run=True)

        output = capsys.readouterr().out
        assert "Files processed: 1" in output
        assert "Files skipped (invalid ChatGPT exports): 1" in output
        assert "Files skipped (ambiguous ChatGPT branches)" not in output
    finally:
        shutil.rmtree(tmpdir)
