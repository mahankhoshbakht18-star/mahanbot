"""Backward-compatible imports for the local CAPTCHA service."""

from api_solver import CaptchaService, solve_captcha_api
from bot_select_captcha import ModelCaptchaSolver

__all__ = ["CaptchaService", "ModelCaptchaSolver", "solve_captcha_api"]
