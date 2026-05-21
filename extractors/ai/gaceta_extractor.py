import os
import json
from groq import Groq
from rich import print as rprint

class GacetaExtractor:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Se requiere una API válida para usar Groq")
        
        # Inicializamos el cliente de Groq
        self.client = Groq(api_key=api_key)
        # Usamos Llama 3 70B, excelente para estructurar JSON
        self.model_name = 'llama-3.1-8b-instant'

    def _build_prompt(self, texto_gaceta: str) -> str:
        instrucciones = """
        Eres un experto analista de datos legislativos. Tu tarea es leer el texto de una gaceta gubernamental y extraer TODOS los registros de votaciones.
        
        1. Identifica el quórum inicial.
        2. Por cada votación, extrae votos a favor, contra y abstenciones.
        3. Calcula Total (Favor + Contra + Abstenciones).
        4. Calcula Ausentes (Quórum inicial - Total).
        
        Devuelve ÚNICA Y EXCLUSIVAMENTE un JSON válido con la siguiente estructura, sin texto adicional:
        {
            "registros": [
                {
                    "Año Legislatura": "Primer año",
                    "Periodo": "Primer periodo",
                    "Fecha": "YYYY-MM-DD",
                    "Tipo": "Punto de Acuerdo",
                    "Titulo": "Acuerdo para...",
                    "Tema 1": 19,
                    "Tema": "Internos del Congreso",
                    "A favor": 0,
                    "Contra": 0,
                    "Abstenciones": 0,
                    "Ausentes": 0,
                    "Total": 0,
                    "Porcentaje de Participación": 0.0,
                    "Unidad": 1
                }
            ]
        }
        """
        return instrucciones + "\n\nTexto de la Gaceta:\n" + texto_gaceta

    def get_data(self, clean_text: str) -> list:
        if not clean_text:
            print("Advertencia: Se recibió texto vacío para analizar.")
            return []

        prompt = self._build_prompt(clean_text)
        
        try:
            print("Consultando a Groq (Llama 3)...")
            
            # Llamada a la API de Groq forzando el formato JSON
            chat_completion = self.client.chat.completions.create(
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                model=self.model_name,
                response_format={"type": "json_object"}
            )
            
            respuesta_texto = chat_completion.choices[0].message.content
            datos_json = json.loads(respuesta_texto)
            
            registros = datos_json.get("registros", [])
            print(f"IA extrajo {len(registros)} registros exitosamente.")
            return registros
            
        except json.JSONDecodeError as e:
            print(f"Error al parsear el JSON de la IA: {e}")
            print(f"Respuesta cruda:{respuesta_texto}")
            return []
        except Exception as e:
            print(f"Error en la API de IA: {e}")
            return []