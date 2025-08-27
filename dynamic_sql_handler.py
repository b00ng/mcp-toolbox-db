# dynamic_sql_handler.py

import sqlite3
import re
from google import genai

class DynamicSQLHandler:
    FORBIDDEN_KEYWORDS = ['insert', 'update', 'delete', 'drop', 'alter', 'create', 'truncate', 'attach']

    def __init__(self, db_path, client):
        self.db_path = db_path
        self.client = client
        self.schema_info = self._get_schema_info()

    def _get_schema_info(self):
        """
        Retrieves the database schema to provide context to the language model.
        """
        return """
        ### Database Schema for SQLite ###
        Table: `customers` (id, name, email, created_at)
        Table: `products` (id, sku, name, price_cents, stock)
        Table: `orders` (id, customer_id, status, created_at) - status is one of 'pending', 'paid', 'shipped', 'cancelled'
        Table: `order_items` (id, order_id, product_id, quantity, price_cents)
        ### Key Relationships ###
        - orders.customer_id -> customers.id
        - order_items.order_id -> orders.id
        - order_items.product_id -> products.id
        """

    def _generate_sql(self, natural_query):
        """
        Generates the SQL query from a natural language prompt using the LLM.
        """
        prompt = f"""
        You are an expert SQL developer. Based on the following database schema,
        write a single, syntactically correct SQLite query to answer the user's question.
        Only return the SQL query, with no additional text, explanation, or markdown.

        Schema:
        {self.schema_info}

        Question: "{natural_query}"
        """

        print("--- Sending to Gemini ---")
        print(prompt)

        # CORRECTED: Use the proper API method
        response = self.client.models.generate_content(
            model='gemini-1.5-flash', 
            contents=[prompt]
        )

        print("--- Received from Gemini ---")
        print(response.text)

        return response.text

    def _extract_sql_from_response(self, llm_response_text):
        """
        Extracts the SQL query from the LLM's response, removing any
        extraneous text or markdown formatting.
        """
        # Fix the regex pattern - it was incomplete
        match = re.search(r"``````", llm_response_text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        
        # Fallback: remove any backticks at start/end
        cleaned = llm_response_text.strip()
        if cleaned.startswith('```'):
            cleaned = cleaned[6:].strip()
        if cleaned.startswith('```'):
            cleaned = cleaned[3:].strip()
        if cleaned.endswith('```'):
            cleaned = cleaned[:-3].strip()
            
        return cleaned

    def _validate_sql(self, sql):
        """
        Validates the SQL to ensure it's a read-only query.
        """
        sql_lower_stripped = sql.strip().lower()
        if not (sql_lower_stripped.startswith('select') or sql_lower_stripped.startswith('with')):
            raise ValueError("Security Error: Query must be a read-only statement starting with SELECT or WITH.")
        
        for keyword in self.FORBIDDEN_KEYWORDS:
            if re.search(r'\b' + keyword + r'\b', sql_lower_stripped):
                raise ValueError(f"Security Alert: Query contains a forbidden keyword: '{keyword}'.")
        
        return True

    def execute_query(self, natural_query, max_results=100):
        """
        Orchestrates the process of generating, validating, and executing the SQL.
        """
        sql_query = "No query generated"
        try:
            # 1. Generate the raw text from the LLM
            raw_response_text = self._generate_sql(natural_query)

            # 2. Extract the clean SQL, removing markdown and extra text
            sql_query = self._extract_sql_from_response(raw_response_text)
            
            if not sql_query:
                raise ValueError("LLM did not return a valid SQL query.")

            # 3. Validate the extracted SQL
            self._validate_sql(sql_query)

            # 4. Sanitize and prepare the SQL for execution
            sanitized_sql = sql_query.strip().rstrip(';')

            # 5. Execute the query
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Execute the sanitized SQL
            cursor.execute(sanitized_sql)
            
            # Fetch a limited number of results
            rows = [dict(row) for row in cursor.fetchmany(max_results)]
            conn.close()

            return {
                "generated_sql": sql_query,
                "executed_sql": sanitized_sql,
                "results": rows,
                "status": "success"
            }

        except (ValueError, sqlite3.Error) as e:
            # Log the error for debugging
            print(f"Error processing query: {e}")
            return {
                "generated_sql": sql_query,
                "error": str(e),
                "status": "error"
            }
