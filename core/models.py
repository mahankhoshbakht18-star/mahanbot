"""
core/models.py — All Pydantic request / response models.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class ApplicantModel(BaseModel):
    id: Optional[int] = None
    full_name: str
    national_id: str
    data: Dict[str, Any]


class SMSRequest(BaseModel):
    nid: str
    code: str


class FavoriteBanksRequest(BaseModel):
    banks: List[str]


class JobStartRequest(BaseModel):
    bot_name: str
    nid: str
    loan_type: Optional[str] = None
    browser_profile_override: Optional[Dict[str, Any]] = None


class BrowserProfileRequest(BaseModel):
    browser: Optional[str] = None
    headless: Optional[bool] = None
    slow_mo_ms: Optional[int] = None
    viewport: Optional[Dict[str, int]] = None
    window_width: Optional[int] = None
    window_height: Optional[int] = None
    window_position: Optional[str] = None
    start_maximized: Optional[bool] = None
    always_on_top: Optional[bool] = None
    remember_position: Optional[bool] = None
    user_data_dir: Optional[str] = None
    proxy: Optional[str] = None
    timeout_ms: Optional[int] = None


class SettingsRequest(BaseModel):
    captcha_delay: Optional[float] = None
    retry_count: Optional[int] = None
    headless: Optional[bool] = None
    browser_headless: Optional[bool] = None
    browser_window_width: Optional[int] = None
    browser_window_height: Optional[int] = None
    browser_window_position: Optional[str] = None
    browser_start_maximized: Optional[bool] = None
    browser_always_on_top: Optional[bool] = None
    browser_remember_position: Optional[bool] = None
    browser_display_mode: Optional[str] = None
    clear_cookies: Optional[bool] = None
    save_only_mode: Optional[bool] = None
    sms_auto_resend: Optional[bool] = None
    captcha_mode: Optional[str] = None
    final_submit: Optional[bool] = None
    auto_final_submit: Optional[bool] = None
    use_proxy: Optional[bool] = None
    proxy_list: Optional[str] = None
    firewall_solve_fast: Optional[bool] = None
    automation_mode: Optional[str] = None
    default_timeout_ms: Optional[int] = None
    navigation_timeout_ms: Optional[int] = None
    retry_delay: Optional[float] = None
    captcha_poll_interval: Optional[float] = None
    captcha_retry_delay: Optional[float] = None
    captcha_max_retries: Optional[int] = None
    captcha_manual_timeout: Optional[int] = None
    pause_for_manual_captcha: Optional[bool] = None
    pause_for_manual_otp: Optional[bool] = None
    otp_validity_seconds: Optional[int] = None
    otp_retry_limit: Optional[int] = None
    otp_hard_stop: Optional[bool] = None
    bank_refresh_interval: Optional[float] = None
    bank_list_refresh_seconds: Optional[float] = None
    bank_capacity_recheck_seconds: Optional[float] = None
    bank_monitor_enabled: Optional[bool] = None
    allow_reserve_bank: Optional[bool] = None
    resume_from_checkpoint: Optional[bool] = None
    save_page_enabled: Optional[bool] = None
    repair_page_enabled: Optional[bool] = None
    diagnostic_logging_enabled: Optional[bool] = None
    save_html: Optional[bool] = None
    save_screenshot: Optional[bool] = None
    save_only_on_bank_change: Optional[bool] = None
    auto_repair_saved_pages: Optional[bool] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class SubscriptionUserRequest(BaseModel):
    username: str
    password: str
    days: int
    is_admin: bool = False


class SubscriptionStateRequest(BaseModel):
    active: bool


class AllowedDomainsRequest(BaseModel):
    domains: List[str]


class AllowlistCheckRequest(BaseModel):
    url: str
