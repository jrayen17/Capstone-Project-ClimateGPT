from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import asyncio
import json
import os
from groq import Groq
from emissions_data import EmissionDataTool  # Specific year data
from emission_data_average import EmissionDataTool_Average  # Average data

# Initialize the FastAPI app
app = FastAPI()

# Mount static files for frontend
static_path = os.path.join(os.path.dirname(__file__), 'static')
app.mount("/static", StaticFiles(directory=static_path), name="static")

# Serve the index.html at the root
@app.get("/")
async def serve_frontend():
    return FileResponse(static_path + "/index.html")

# Set the Groq API key for the LLaMA model
GROQ_API_KEY = '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'

# Declaring the Groq API client
groq_client = Groq(api_key=GROQ_API_KEY)

# LLaMA Model Versions
llama_70B_tool_use = 'llama3-groq-70b-8192-tool-use-preview'
llama_70B_response_generation = 'llama3-groq-70b-8192-tool-use-preview'

# Emission Query Handling Class
class EmissionQueryHandler:
    def __init__(self):
        self.tool_specific = EmissionDataTool()  # Tool for specific year data
        self.tool_average = EmissionDataTool_Average()  # Tool for average data

    # Function to handle both specific year and average queries
    async def fetch_emission_data(self, function_name: str, parameters: dict):
        # Dynamically call the respective tool based on the function name
        if function_name == "get_emission_data":
            return await self.tool_specific.run_impl(**parameters)
        elif function_name == "get_average_emission_data":
            return await self.tool_average.run_impl(**parameters)
        else:
            return "Unknown function requested by the model."

# Tool declaration in the form of a JSON block
def get_tool_declaration():
    return """
    <tools> {
        "name": "get_emission_data",
        "description": "Get emission values for a given country, year, and emission type.",
        "parameters": {
            "properties": {
                "country": {
                    "description": "The name of the country or area.",
                    "type": "string"
                },
                "year": {
                    "description": "The year of the emission data.",
                    "type": "integer"
                },
                "emission_type": {
                    "description": "The type of emission (optional).",
                    "type": "string"
                }
            },
            "required": ["country", "year"],
            "type": "object"
        }
    },
    {
        "name": "get_average_emission_data",
        "description": "Get the average emission value for a country across all years for a specified emission type.",
        "parameters": {
            "properties": {
                "country": {
                    "description": "The name of the country or area.",
                    "type": "string"
                },
                "emission_type": {
                    "description": "The type of emission (optional).",
                    "type": "string"
                }
            },
            "required": ["country", "emission_type"],
            "type": "object"
        }
    } </tools>
    """

# API Endpoint to process user questions
class UserQuestion(BaseModel):
    question: str

@app.post("/ask")
async def ask_question(user_question: UserQuestion):
    question = user_question.question
    tool_declaration = get_tool_declaration()

    # Query the LLaMA model (Groq API) to determine which tool to use
    chat_completion = groq_client.chat.completions.create(
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are a function-calling AI model. You have access to the following tools: {tool_declaration}. "
                    "For each function call, return a JSON object with the function name and the arguments required. "
                    "Consider the meaning and context of different user queries, even if phrased differently, and map them to the appropriate tool."
                )
            },
            {"role": "user", "content": question},
        ],
        model=llama_70B_tool_use,
    )

    llama_response = chat_completion.choices[0].message.content
    function_and_parameters = extract_function_and_parameters(llama_response)

    if function_and_parameters:
        # Extract function name and parameters
        function_name = function_and_parameters.get("function_name")
        parameters = function_and_parameters.get("arguments")

        if function_name and parameters:
            # Call the appropriate emission tool using EmissionQueryHandler
            query_handler = EmissionQueryHandler()
            emission_data = await query_handler.fetch_emission_data(
                function_name=function_name,
                parameters=parameters
            )

            # Use LLaMA model to generate a descriptive response based on the user query and emission data
            detailed_response_prompt = (
                f"The user asked: '{question}'. The emission data retrieved is: '{emission_data}'. "
                f"Please generate a descriptive response that clearly explains the result to the user."
            )

            response_generation = groq_client.chat.completions.create(
                messages=[
                    {"role": "user", "content": detailed_response_prompt}
                ],
                model=llama_70B_response_generation,
            )

            descriptive_response = response_generation.choices[0].message.content
            return JSONResponse(content={"response": descriptive_response})

    return JSONResponse(content={"response": "Could not extract the function name or parameters."})

# Function to parse the LLaMA model response and extract the function and parameters
def extract_function_and_parameters(response: str):
    try:
        tool_call_start = response.find("<tool_call>")
        tool_call_end = response.find("</tool_call>")
        if tool_call_start == -1 or tool_call_end == -1:
            return None

        tool_call_json = response[tool_call_start + len("<tool_call>"):tool_call_end]
        tool_call_data = json.loads(tool_call_json)

        return {
            "function_name": tool_call_data.get("name"),
            "arguments": tool_call_data.get("arguments"),
        }
    except Exception as e:
        return None