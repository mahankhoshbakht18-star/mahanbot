# -*- coding: utf-8 -*-
"""Field catalog taken from the saved SADAF form in the fuel project.

بخش مصرف options are the option texts of select#ddlKarbordType in
«فرم درخواست فرآورده.html», excluding the placeholder «انتخاب کنید».
The dependent lists ddlKarbordGroups, ddlKarbordss and ddlUsersKarbords
contain only «همه» or nothing in that saved page, because the site fills
them after a postback. Those names stay free text. No option was invented.
"""
import json
from pathlib import Path

_SECTOR_FILE = Path(__file__).with_name("sectors.json")
SECTORS = tuple(json.loads(_SECTOR_FILE.read_text(encoding="utf-8")))

# Names copied from the Mehrestan seller list on the live request form.
SELLERS = (
    "تعاوني زابلي",
    "تعاوني گروك",
    "عشايري لد زابلي",
    "عشايري چدزابلي",
    "فروشندگي نفت سفيد شهري شماره يك زابلي",
    "تعاوني پرکن-زابلي",
    "تعاوني چگرد زابلي",
    "تعاوني دهي زابلي",
    "تعاوني عزيز آباد",
    "تعاوني گدامدر زابلي",
    "تعاوني گرسامان زابلي",
    "تعاوني کهن شهر-زابلي",
    "تعاوني کهن مگار-زابلي",
    "تعاوني کوران زابلي",
    "تعاوني قادر آباد-زابلي",
    "تعاوني رگنتک زابلي",
    "فروشندگي عشايري ناك پهن زابلي",
    "فروشندگي عشايري كاشيكا يي",
    "فروشندگي تعاوني روستايي نوكان",
    "فروشندگي تعاوني روستايي افشان",
    "فروشندگي تعاوني روستايي گزاويز",
    "فروشندگي تعاوني روستايي پتكان",
    "فروشندگي تعاوني روستايي آشار",
    "فروشندگي تعاوني روستايي ايرافشان",
    "فروشندگي داخل اتاقك سيد الشهدا ء",
    "فروشندگي نفت سفيد تعاوني روستايي هيز آباد",
    "تعاوني چاهوك مهرستان",
)

# Sellers that carry نفتگاز. The others sell only نفت سفید.
NAFTGAZ_SELLERS = (
    "تعاوني زابلي",
    "تعاوني پرکن-زابلي",
    "تعاوني کهن مگار-زابلي",
    "تعاوني قادر آباد-زابلي",
    "فروشندگي داخل اتاقك سيد الشهدا ء",
)

NUMBER_FIELDS = (
    "آمار تولید دوره کشت قبل",
    "سطح زیر کشت فعال",
    "نفتگاز|مقدار درخواستی در بخش یارانه",
    "نفتگاز|مقدار درخواستی در بخش غیریارانه",
    "نفتگاز|مانده موجودی",
    "بنزین|مقدار درخواستی در بخش یارانه",
)

GROUPS = (
    {
        "title": "مسیر مصرف",
        "hint": "فهرست «بخش مصرف» از فرم ذخیره‌شدهٔ سامانه است. گروه کاربرد، کاربرد و کاربرد مورد نظر در همان صفحه هنوز گزینه‌ای جز «همه» ندارند و بعد از انتخاب بخش مصرف در خود سایت پر می‌شوند؛ متن دقیق گزینه را بنویسید.",
        "fields": (
            {"name": "بخش مصرف", "kind": "select"},
            {"name": "گروه کاربرد", "kind": "text"},
            {"name": "کاربرد", "kind": "text"},
            {"name": "کاربرد مورد نظر", "kind": "text"},
        ),
    },
    {
        "title": "دوره کشت",
        "hint": "",
        "fields": (
            {"name": "آمار تولید دوره کشت قبل", "kind": "number"},
            {"name": "نوع کشت", "kind": "text"},
            {"name": "سطح زیر کشت فعال", "kind": "number"},
            {"name": "شروع دوره کشت", "kind": "text", "placeholder": "روز/ماه/سال"},
            {"name": "مدت زمان کشت", "kind": "text"},
            {"name": "نوع مصرف سوخت", "kind": "text"},
            {"name": "از تاریخ", "kind": "text", "placeholder": "روز/ماه/سال"},
            {"name": "تا تاریخ", "kind": "text", "placeholder": "روز/ماه/سال"},
        ),
    },
    {
        "title": "فروشنده",
        "hint": "تعاونی را از فهرست مهرستان انتخاب کنید. بات همان نام را جستجو می‌کند، ردیف را برمی‌دارد و تایید را می‌زند.",
        "fields": (
            {"name": "نوع فروشندگی", "kind": "text"},
            {"name": "نام فروشندگی", "kind": "select"},
        ),
    },
    {
        "title": "مقدار فرآورده",
        "hint": "خانهٔ خالی در سایت دست نمی‌خورد. برای مشتری صنعتی فقط همین لیتر و سطح زیر کشت فرق می‌کند.",
        "fields": (
            {"name": "نفتگاز|مقدار درخواستی در بخش یارانه", "kind": "number"},
            {"name": "نفتگاز|مقدار درخواستی در بخش غیریارانه", "kind": "number"},
            {"name": "نفتگاز|مانده موجودی", "kind": "number"},
            {"name": "بنزین|مقدار درخواستی در بخش یارانه", "kind": "number"},
        ),
    },
    {
        "title": "توضیح",
        "hint": "",
        "fields": (
            {"name": "خلاصه دلیل شما برای نیاز به نفتگاز", "kind": "long"},
        ),
    },
)


def field_names():
    return tuple(field["name"] for group in GROUPS for field in group["fields"])


def public_catalog() -> dict:
    return {
        "source": "ddlKarbordType در فرم درخواست فرآورده.html",
        "sectors": list(SECTORS),
        "sellers": list(SELLERS),
        "naftgaz_sellers": list(NAFTGAZ_SELLERS),
        "numbers": list(NUMBER_FIELDS),
        "groups": [
            {"title": group["title"], "hint": group["hint"], "fields": [dict(field) for field in group["fields"]]}
            for group in GROUPS
        ],
    }
