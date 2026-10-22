"""Resolve optional per-applicant submission preferences without losing explicit False."""
import math


def validate_submission_settings(data):
    if data.get('auto_final_submit') is not None and not isinstance(data['auto_final_submit'], bool):
        raise ValueError('ثبت نهایی خودکار باید روشن، خاموش یا تابع تنظیم عمومی باشد.')
    value = data.get('final_submit_interval_seconds')
    if value is not None:
        if isinstance(value, bool):
            raise ValueError('فاصله تلاش باید عددی بین ۱ و ۶۰۰ ثانیه باشد.')
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError('فاصله تلاش باید عددی بین ۱ و ۶۰۰ ثانیه باشد.') from None
        if not math.isfinite(value) or not 1 <= value <= 600:
            raise ValueError('فاصله تلاش باید عددی بین ۱ و ۶۰۰ ثانیه باشد.')
        data['final_submit_interval_seconds'] = value
    return data


def submission_policy(config, applicant):
    enabled = applicant.get('auto_final_submit')
    if not isinstance(enabled, bool):
        enabled = bool(config.get('auto_final_submit', config.get('final_submit', False)))
    inherited = config.get('bank_capacity_check_interval', config.get('bank_capacity_recheck_seconds', 20))
    value = applicant.get('final_submit_interval_seconds')
    try:
        interval = float(inherited if value is None else value)
        if not math.isfinite(interval) or not 1 <= interval <= 600:
            raise ValueError()
    except (TypeError, ValueError):
        interval = 20.0
    return enabled, interval
