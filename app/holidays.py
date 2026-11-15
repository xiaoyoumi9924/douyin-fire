"""Verified, year-specific greetings. Never repeat lunar dates as MM-DD."""
from datetime import date

VALID_FROM = "2026-11-13"
VALID_UNTIL = "2027-11-13"
SOURCE = "https://www.hko.gov.hk/tc/gts/time/calendar/pdf/files/2027.pdf"
# Celebratory festivals only; Qingming is deliberately not given a '快乐' greeting.
HOLIDAYS = (
    ("2026-12-22", "冬至", "冬至快乐"),
    ("2027-01-01", "元旦", "新年快乐"),
    ("2027-02-05", "除夕", "除夕快乐"),
    ("2027-02-06", "春节", "新年快乐"),
    ("2027-02-20", "元宵节", "元宵节快乐"),
    ("2027-05-01", "劳动节", "劳动节快乐"),
    ("2027-06-09", "端午节", "端午节快乐"),
    ("2027-08-08", "七夕节", "七夕节快乐"),
    ("2027-09-15", "中秋节", "中秋节快乐"),
    ("2027-10-01", "国庆节", "国庆节快乐"),
    ("2027-10-08", "重阳节", "重阳节快乐"),
)


def greeting_for(day):
    return next(({"date": when, "name": name, "text": text} for when, name, text in HOLIDAYS if when == day), None)


def calendar_from(today: date):
    return [{"date": when, "name": name, "text": text} for when, name, text in HOLIDAYS if when >= today.isoformat()]
