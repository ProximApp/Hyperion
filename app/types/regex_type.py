from enum import Enum


class EmailRegex(Enum):
    ECL_STAFF_REGEX = r"^[\w\-.]*@(enise\.)?ec-lyon\.fr$"
    ECL_STUDENT_REGEX = r"^[\w\-.]*@((etu(-enise)?)|(ecl\d{2}))\.ec-lyon\.fr$"
    ECL_FORMER_STUDENT_REGEX = r"^[\w\-.]*@centraliens-lyon\.net$"

    EMLYON_STUDENT_REGEX = r"^[\w\-.]*@edu\.em-lyon\.com$"
