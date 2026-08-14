import json
import os

from openai import OpenAI

_client = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def pedir_decision_json(system_prompt: str, contexto: dict) -> dict:
    """Llama al modelo con el contexto de un agente y devuelve su decision como dict.

    El prompt de cada agente exige una respuesta JSON con forma fija, asi el resultado
    se puede volcar directo a las columnas de DecisionLog sin parseo de texto libre.
    """
    client = get_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(contexto, ensure_ascii=False, default=str)},
        ],
    )
    return json.loads(response.choices[0].message.content)
