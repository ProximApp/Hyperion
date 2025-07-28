from enum import Enum


class EmailRegexName(str, Enum):
    ECL_STAFF_REGEX = "ECL_STAFF_REGEX"
    ECL_STUDENT_REGEX = "ECL_STUDENT_REGEX"
    ECL_FORMER_STUDENT_REGEX = "ECL_FORMER_STUDENT_REGEX"

    EMLYON_STUDENT_REGEX = "EMLYON_STUDENT_REGEX"


class EmailRegex(str, Enum):
    ECL_STAFF_REGEX = r"^[\w\-.]*@(enise\.)?ec-lyon\.fr$"
    ECL_STUDENT_REGEX = r"^[\w\-.]*@((etu(-enise)?)|(ecl\d{2}))\.ec-lyon\.fr$"
    ECL_FORMER_STUDENT_REGEX = r"^[\w\-.]*@centraliens-lyon\.net$"

    EMLYON_STUDENT_REGEX = r"^[\w\-.]*@edu\.em-lyon\.com$"
