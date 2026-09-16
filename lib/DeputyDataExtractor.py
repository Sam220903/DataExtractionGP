import os
import re
import unicodedata

genderPattern = re.compile(r"diputad([oa])", re.IGNORECASE)


def extractGenderFromText(text):
    """
    Searches a text string for "diputado" or "diputada"
    and returns "Masculino", "Femenino", or None if not found.
    """
    if not text:
        return None

    match = genderPattern.search(text)

    if not match:
        return None

    return "Masculino" if match.group(1).lower() == "o" else "Femenino"


def extractGenderFromCandidates(candidateTexts):
    """
    Receives a list of text candidates, in order of reliability,
    and returns the gender found in the first one that matches.
    """
    for text in candidateTexts:
        gender = extractGenderFromText(text)
        if gender is not None:
            return gender

    raise ValueError("No se pudo determinar el género en ninguna fuente disponible")


def getPartyAbbreviationFromFileName(fileName):
    """
    Receives a party logo file name (e.g. "mc.png") and returns
    its abbreviation in uppercase (e.g. "MC").
    """
    partyName = os.path.splitext(fileName)[0]
    return partyName.upper()


def normalizeRoleText(roleText):
    """
    Removes accents and lowercases a role text so it can be
    compared reliably (e.g. "Presidenta" -> "presidenta").
    """
    normalized = unicodedata.normalize("NFKD", roleText)
    return normalized.encode("ascii", "ignore").decode("ascii").strip().lower()


def classifyRole(roleText):
    """
    Classifies a role text into "presidente", "secretario" or "vocal".
    """
    normalizedRole = normalizeRoleText(roleText)

    if normalizedRole.startswith("presiden"):
        return "presidente"

    if normalizedRole.startswith("secretari"):
        return "secretario"

    return "vocal"


def buildCommitteeMembersData(membersRawData):
    """
    Receives a list of dicts, one per member, each already carrying
    plain values under "name", "role", "gender" and "party" (extracted
    beforehand from the HTML), and returns a dict with the aggregated
    member data: counts, president, secretary and vocals.
    """
    membersCount = len(membersRawData)
    menCount = 0
    womenCount = 0

    presidentName = ""
    presidentParty = ""
    presidentGender = ""

    secretaryName = ""
    secretaryParty = ""
    secretaryGender = ""

    vocals = []

    for memberData in membersRawData:
        name = memberData["name"]
        role = memberData["role"]
        gender = memberData["gender"]
        party = memberData["party"]

        if gender == "Masculino":
            menCount += 1
        else:
            womenCount += 1

        roleType = classifyRole(role)

        if roleType == "presidente":
            presidentName = name
            presidentParty = party
            presidentGender = gender
        elif roleType == "secretario":
            secretaryName = name
            secretaryParty = party
            secretaryGender = gender
        else:
            vocals.append({"nombre": name, "partido": party})

    membersData = {
        "Número de integrantes": membersCount,
        "Hombres": menCount,
        "Mujeres": womenCount,
        "PresHM": 1 if presidentGender == "Femenino" else 0,
        "Presidencia género": presidentGender,
        "Presidente / Presidenta": presidentName,
        "Partido presidente": presidentParty,
        "SecHM": 1 if secretaryGender == "Femenino" else 0,
        "Secretario (a)": secretaryName,
        "Partido secretario": secretaryParty,
        "Número de secretarios": 1 if secretaryName else 0,
        "Número de vocales": len(vocals),
    }

    for index, vocal in enumerate(vocals, start=1):
        membersData[f"Vocal {index}"] = vocal["nombre"]
        membersData[f"Partido {index}"] = vocal["partido"]

    return membersData