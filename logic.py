import json
import os
import urllib.request

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

CATEGORIES = ["Academic", "Facilities", "Finance", "Registrar", "IT / Technical", "Student Services", "Other"]
STATUSES = ["Pending", "In Progress", "Resolved"]


def prepare_text(text):
    for ch in ",.;:!?()/":
        text = text.replace(ch, " ")
    return " " + " ".join(text.lower().split()) + " "


class KeywordClassifier:
    KEYWORDS = {
        "Academic": ["grade", "grades", "subject", "professor", "teacher", "instructor", "exam", "quiz",
                     "class", "schedule", "assignment", "lesson", "thesis", "attendance", "project"],
        "Facilities": ["room", "aircon", "air-con", "fan", "light", "restroom", "comfort", "cr", "chair",
                       "table", "broken", "leak", "dirty", "clean", "library", "canteen", "parking"],
        "Finance": ["tuition", "payment", "fee", "fees", "balance", "cashier", "scholarship", "refund",
                    "installment", "receipt", "discount"],
        "Registrar": ["enroll", "enrollment", "tor", "transcript", "certificate", "document", "documents",
                      "records", "shifting", "drop", "credential", "diploma"],
        "IT / Technical": ["wifi", "wi-fi", "internet", "portal", "password", "account", "login", "computer",
                           "laptop", "printer", "website", "system", "email", "lms"],
        "Student Services": ["guidance", "counseling", "bullying", "id", "clinic", "organization", "event",
                             "discipline", "harassment", "uniform", "health", "medical"],
    }

    def predict(self, text):
        text = prepare_text(text)
        best_category, best_score = "Other", 0
        for category, words in self.KEYWORDS.items():
            score = sum(1 for w in words if " " + w + " " in text)
            if score > best_score:
                best_score, best_category = score, category
        return best_category


class RuleResponder:
    OFFICES = {
        "Academic": "the Program Head / your subject instructor",
        "Facilities": "the Maintenance / Property Office",
        "Finance": "the Cashier / Accounting Office",
        "Registrar": "the Registrar's Office",
        "IT / Technical": "the IT Department",
        "Student Services": "the Guidance / Student Affairs Office",
        "Other": "the Student Affairs Office",
    }
    GENERAL = {
        "Academic": "For concerns about your class, it is best to talk to your subject instructor first. "
                    "If it is still not solved, you can bring it to your Program Head.",
        "Facilities": "Please include the building, the room number and what exactly is broken or dirty "
                      "so that the maintenance staff can find and fix it faster.",
        "Finance": "Please prepare your official receipts and your school ID when you visit the cashier, "
                   "so your account can be checked quickly.",
        "Registrar": "Requests about records and documents are handled by the Registrar. "
                     "Please bring a valid ID and allow a few working days for processing.",
        "IT / Technical": "Please write down the room, the time and the device you used when the problem "
                          "happened. This helps the IT staff check it.",
        "Student Services": "Your concern is important. You are also welcome to visit the office in person "
                            "if you want to talk to somebody.",
        "Other": "We are not sure which office handles this, so a teacher will read it and guide you.",
    }
    TIPS = [
        (["bullying", "harassment", "threat", "abuse"],
         "We are sorry this happened to you. Your safety comes first. Please tell a teacher or go to the "
         "Guidance Office as soon as you can. We marked this concern as important."),
        (["wifi", "wi-fi", "internet"],
         "For WiFi problems, try to forget the network and connect again, then restart your device."),
        (["password", "login", "account", "portal"],
         "For account problems, try the 'forgot password' option first. If it still does not work, "
         "bring your school ID to the IT office so your account can be reset."),
        (["grade", "grades"],
         "For grade concerns, please prepare your quizzes, exams or class record so your instructor can check them."),
        (["tuition", "balance", "payment", "receipt"],
         "For payment concerns, please keep all your official receipts and ask the cashier to check your balance."),
        (["scholarship", "discount"],
         "For scholarship or discount concerns, please bring the requirements or letter you received."),
        (["enroll", "enrollment", "drop", "shifting"],
         "For enrollment concerns, please check with the Registrar the schedule and the requirements for enrollment."),
        (["tor", "transcript", "certificate", "diploma", "document", "documents"],
         "For document requests, please fill out the request form at the Registrar and bring a valid ID."),
        (["aircon", "air-con", "fan", "light", "leak", "broken", "restroom", "comfort"],
         "Thank you for reporting the problem. Please tell us the exact room so it can be checked."),
        (["printer", "computer", "laptop"],
         "For computer or printer problems, please tell us the laboratory and the unit number."),
    ]

    def reply(self, title, description, category):
        text = prepare_text(title + " " + description)
        lines = ["Hello! Thank you for sending your concern."]
        added = 0
        for words, tip in self.TIPS:
            if any(" " + w + " " in text for w in words):
                lines.append(tip)
                added += 1
                if added == 2:
                    break
        lines.append(self.GENERAL[category])
        lines.append("Your concern is under '" + category + "' and was sent to " + self.OFFICES[category] +
                     ". A teacher will follow up. You can check the status in My Concerns.")
        return "\n\n".join(lines)


class ClaudeResponder:
    def __init__(self, api_key, backup):
        self.api_key = api_key
        self.backup = backup

    def reply(self, title, description, category):
        try:
            question = "Category: " + category + "\nTitle: " + title + "\nConcern: " + description
            body = json.dumps({
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 300,
                "system": "You are the helpful assistant of ICCT Colleges Foundation Inc. Answer the concern "
                          "of a student politely in 3 to 5 short sentences. Use simple English. Say which "
                          "office can help. Do not invent school policies or promise results. End by telling "
                          "the student that a teacher will follow up.",
                "messages": [{"role": "user", "content": question}],
            }).encode()
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages", data=body,
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())["content"][0]["text"]
        except Exception:
            return self.backup.reply(title, description, category)


def make_responder():
    rule = RuleResponder()
    return ClaudeResponder(API_KEY, rule) if API_KEY else rule


def make_summary(rows, stamp):
    lines = ["ICCT STUDENT CONCERN REPORT", "Generated: " + stamp,
             "Total concerns: " + str(len(rows)), "", "By Category:"]
    for c in CATEGORIES:
        n = len([r for r in rows if r["category"] == c])
        if n:
            lines.append("  " + c.ljust(20) + str(n))
    lines += ["", "By Status:"]
    for s in STATUSES:
        lines.append("  " + s.ljust(20) + str(len([r for r in rows if r["status"] == s])))
    lines += ["", "By Date:"]
    for d in sorted(set(r["date_created"][:10] for r in rows), reverse=True):
        lines.append("  " + d.ljust(20) + str(len([r for r in rows if r["date_created"][:10] == d])))
    return "\n".join(lines)
