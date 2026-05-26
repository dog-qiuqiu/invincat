import sys

from invincat_cli.built_in_skills.dependency_check import require_module

pypdf = require_module("pypdf", "pdf")
PdfReader = pypdf.PdfReader

reader = PdfReader(sys.argv[1])
if (reader.get_fields()):
    print("This PDF has fillable form fields")
else:
    print("This PDF does not have fillable form fields; you will need to visually determine where to enter data")
