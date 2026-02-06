def main() -> int:
    from database import DBHandler

    sample = DBHandler.get_applicant("NON_EXISTENT")
    if sample is not None and not isinstance(sample, dict):
        print("ERROR: get_applicant should return dict or None")
        return 1

    print("OK: get_applicant returns dict or None")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
