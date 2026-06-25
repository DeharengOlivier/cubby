from cubby.cli import main


def test_plan_lists_without_moving(tmp_path, capsys):
    (tmp_path / "Invoice-1.pdf").write_text("x")
    (tmp_path / "photo.png").write_text("x")
    rc = main(["plan", "--source", str(tmp_path), "--no-content"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Invoices/" in out
    assert "Images/" in out
    assert (tmp_path / "Invoice-1.pdf").exists()  # nothing moved


def test_run_moves_files(tmp_path, capsys):
    (tmp_path / "Invoice-1.pdf").write_text("x")
    rc = main(["run", "--source", str(tmp_path), "--delay", "0", "--no-content"])
    assert rc == 0
    assert (tmp_path / "Invoices" / "Invoice-1.pdf").exists()


def test_doctor_runs(capsys):
    rc = main(["doctor", "--source", "/tmp"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "service backend" in out


def test_run_then_undo_via_cli(tmp_path, capsys, monkeypatch):
    from cubby.adapters import journal as journal_mod

    monkeypatch.setattr(journal_mod, "DEFAULT_JOURNAL", tmp_path / "journal.jsonl")
    (tmp_path / "Invoice-1.pdf").write_text("x")

    main(["run", "--source", str(tmp_path), "--delay", "0", "--no-content"])
    assert (tmp_path / "Invoices" / "Invoice-1.pdf").exists()

    rc = main(["undo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Restored 1 file" in out
    assert (tmp_path / "Invoice-1.pdf").exists()


def test_missing_source_errors(tmp_path, capsys):
    rc = main(["run", "--source", str(tmp_path / "nope")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "does not exist" in err


def test_status_runs(capsys):
    rc = main(["status"])
    assert rc == 0
    assert "agent installed" in capsys.readouterr().out


def test_plan_json_output(tmp_path, capsys):
    import json

    (tmp_path / "photo.png").write_text("x")
    main(["plan", "--source", str(tmp_path), "--no-content", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["items"][0]["category"] == "Images"


def test_no_command_shows_banner_and_help(capsys):
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cubby" in out
    assert "usage:" in out  # help printed
