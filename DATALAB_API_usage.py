from datalab_sdk import DatalabClient, ConvertOptions
import os
from dotenv import load_dotenv
load_dotenv()

DATALAB_API_KEY = os.getenv("DATALAB_API_KEY")

client = DatalabClient(DATALAB_API_KEY)

# With options
options = ConvertOptions(
    output_format="html",
    mode="balanced",
    paginate=True,
    max_pages = 20,
    disable_image_extraction = True
)
result = client.convert("input.pdf", options=options)
print(f"Quality score: {result.parse_quality_score}")
print(result.html)