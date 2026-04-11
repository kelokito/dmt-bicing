import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

class DatabaseConnector:
    def __init__(self, env_path='.env'):
        load_dotenv(dotenv_path=env_path)
        
        self.host = os.getenv("PG_HOST")
        self.database = os.getenv("PG_DATABASE")
        self.user = os.getenv("PG_USER")
        self.password = os.getenv("PG_PASSWORD")
        self.port = os.getenv("PG_PORT")
        
        self.connection = None

    def connect(self):
        if self.connection is None or self.connection.closed != 0:
            try:
                self.connection = psycopg2.connect(
                    host=self.host,
                    database=self.database,
                    user=self.user,
                    password=self.password,
                    port=self.port
                )
                print("Database connection established successfully.")
            except (Exception, psycopg2.DatabaseError) as error:
                print(f"Error connecting to the database: {error}")
                raise

    def disconnect(self):
        if self.connection and self.connection.closed == 0:
            self.connection.close()
            print("Database connection closed.")

    def execute_query(self, query, params=None, fetch=False):
        """
        Execute an SQL query against the database.
        
        Parameters:
        - query (str): The SQL query to execute.
        - params (tuple/dict): Parameters for the query (avoids SQL injection).
        - fetch (bool): True if the query returns data (SELECT), False for DML or DDL.
        
        Returns:
        - List of dictionaries with the results if fetch=True, None if fetch=False.
        """
        self.connect()
        
        with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            try:
                cursor.execute(query, params)
                
                if fetch:
                    result = cursor.fetchall()
                    self.connection.commit()
                    return result
                else:
                    self.connection.commit()
                    return None
                    
            except (Exception, psycopg2.DatabaseError) as error:
                self.connection.rollback()
                print(f"Error executing the SQL query:\n{query}\nError: {error}")
                raise