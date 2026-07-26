from scripts.check_captcha_freeze import main


def test_captcha_files_are_frozen() -> None:
    assert main() == 0
