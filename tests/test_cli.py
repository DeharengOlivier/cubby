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


def test_no_command_errors(capsys):
    import pytest

    with pytest.raises(SystemExit):
        main([])
