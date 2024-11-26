import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values
import pandas as pd
import warnings
import numpy as np
import logging

# Configure logging
logging.basicConfig(
    filename="migration.log",
    filemode="w",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

warnings.filterwarnings("ignore")


class DBManager:
    def __init__(
        self,
        dbname="central_health",
        user="postgres",
        password="1234",
        host="localhost",
        port=5432,
    ):
        self.dbname = dbname
        self.user = user
        self.password = password
        self.host = host
        self.port = port
        self.conn = None

    def connect(self):
        try:
            # Attempt to connect to the database
            self.conn = psycopg2.connect(
                dbname=self.dbname,
                user=self.user,
                password=self.password,
                host=self.host,
                port=self.port,
            )
            logging.info("Connected to the database.")
        except psycopg2.OperationalError as e:
            logging.warning(f"Database connection failed: {e}")
            raise

        return self.conn


class Migration:
    def __init__(self, conn: DBManager.connect, schema_file="schema.sql"):
        self.schema_file = schema_file
        self.conn = conn

    def delete_all_tables(self):
        try:
            with self.conn.cursor() as cursor:
                # Get a list of all table names in the current database
                cursor.execute(
                    """
                    SELECT tablename
                    FROM pg_tables
                    WHERE schemaname = 'public';
                    """
                )
                tables = cursor.fetchall()

                # Drop each table
                for table in tables:
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
                            sql.Identifier(table[0])
                        )
                    )
                    logging.info(f"Table '{table[0]}' dropped.")

                self.conn.commit()
                logging.info("All tables deleted successfully.")
        except Exception as e:
            logging.error(f"Failed to delete tables: {e}", exc_info=True)
            raise

    def apply_schema(self):
        try:
            with self.conn.cursor() as cursor:
                with open(self.schema_file, "r") as schema:
                    cursor.execute(schema.read())
                    self.conn.commit()
                    logging.info("Schema applied successfully.")
        except Exception as e:
            logging.error(f"Failed to apply schema: {e}", exc_info=True)
            raise

    def import_all(self):
        try:
            self._import_account()
            self._import_rad_type()
            self._import_rad()
            self._import_business_unit()
            self._import_account_rad_type()
            self._import_budget()
            self._import_budget_rad()
            self._import_journal_entry()
            self._import_journal_entry_rad()
            self._import_budget_entry_admin_view()
            logging.info("All data imported successfully.")
        except Exception as e:
            logging.exception(f"Error during import_all: {e}")

    def _import_account(self):
        try:
            account_raw = pd.read_csv(r"./source/Account.csv")
            logging.info("Account data read from CSV.")
        except Exception as e:
            logging.exception(f"Error Account CSV: {e}")
            return

        try:
            account_ownership_raw = pd.read_csv(r"./source/AccountOwnership.csv")
            logging.info("Account data read from CSV.")
        except Exception as e:
            logging.exception(f"Error Account CSV: {e}")
            return

        account_df = account_raw.copy()
        account_df = Util.sanitize_columns(account_df)
        account_df.dropna(inplace=True, subset="chart_id")
        account_df["chart_id"] = account_df["chart_id"].astype(int)

        account_ownership_df = account_ownership_raw.copy()
        account_ownership_df = Util.sanitize_columns(account_ownership_df)
        account_ownership_df.dropna(inplace=True, subset="account_no")
        account_ownership_df = account_ownership_df[["account_no", "parent_key_id"]]
        account_ownership_df["parent_key_id"] = account_ownership_df[
            "parent_key_id"
        ].str.replace("1_", "")
        merge = pd.merge(account_df, account_ownership_df, on="account_no")
        merge.rename(columns={"parent_key_id": "parent_account_no"}, inplace=True)
        merge["date_created"] = pd.to_datetime(merge["date_created"], errors="coerce")
        merge["date_created"] = merge["date_created"].astype("datetime64[ns]")

        table = "account"
        Util.import_func(merge, table, self.conn)

    def _import_account_rad_type(self):
        try:
            raw = pd.read_csv(r"./source/Rad_Account.csv")
            logging.info("RAD Account data read from CSV.")
        except Exception as e:
            logging.exception(f"Error RAD Account CSV: {e}")
            return

        rad_account = raw.copy()
        rad_account = Util.sanitize_columns(rad_account)
        rad_account["account_no"] = rad_account["account_no"].astype(str)

        sql = "SELECT id, account_no FROM account;"
        accounts = Util.execute_query(sql, self.conn)
        accounts["account_no"] = accounts["account_no"].astype(str)
        merge = pd.merge(rad_account, accounts, on="account_no")
        merge = merge[["rad_type_id", "id"]]
        merge = merge.rename(columns={"rad_type_id": "rad_type_id", "id": "account_id"})
        merge.dropna(inplace=True)

        table = "account_rad_type"
        Util.import_func(merge, table, self.conn)

    def _import_budget_rad(self):
        try:
            raw = pd.read_csv(r"./source/budget.csv")
            logging.info("Budget RAD data read from CSV.")
        except Exception as e:
            logging.exception(f"Error reading Budget RAD CSV: {e}")
            return

        df = raw.copy()
        df = Util.sanitize_columns(df)
        df = df.reset_index(drop=True)
        df["id"] = df.index + 1

        df_split = df.copy()
        cols = [
            col for col in df_split.columns if "rad" in col and "description" not in col
        ]
        df_split[cols] = df_split[cols].applymap(
            lambda x: str(x) if not pd.isna(x) else np.nan
        )
        df_split["Combined"] = df_split[cols].apply(
            lambda row: "|".join([str(x) for x in row if not pd.isna(x)]), axis=1
        )
        df_split = df_split.assign(value=df_split["Combined"].str.split("|")).explode(
            "value"
        )
        df_result = df_split[["id", "value"]].reset_index(drop=True)
        df_result.replace("", float("NaN"), inplace=True)
        df_result.dropna(inplace=True)
        df_result.rename(columns={"value": "rad_id", "id": "budget_id"}, inplace=True)

        table = "budget_rad"
        Util.import_func(df_result, table, self.conn)

    def _import_budget(self):
        try:
            raw = pd.read_csv(r"./source/Budget.csv")
            logging.info("Budget data read from CSV.")
        except Exception as e:
            logging.exception(f"Error reading Budget CSV: {e}")
            return

        df = raw.copy()
        df.columns = Util.sanitize_columns(df)
        df = df.reset_index(drop=True)
        df["id"] = df.index + 1

        df = df[["id", "budget_id", "business_unit_id", "account_no", "amount"]]
        df["account_no"] = df["account_no"].astype(str).str.replace(".0", "")
        df["amount"] = df["amount"].str.replace(",", "").fillna(0).astype(float)

        table = "budget"
        Util.import_func(df, table, self.conn)

    def _import_journal_entry_rad(self):
        try:
            raw = pd.read_csv(r"./source/JournalEntry.csv")
            logging.info("Journal Entry read from CSV.")
        except Exception as e:
            logging.exception(f"Error reading Journal Entry CSV: {e}")
            return

        df = raw.copy()
        df = df.reset_index(drop=True)
        df.columns = Util.sanitize_columns(df)
        df["id"] = df.index + 1

        df["accounting_date"] = pd.to_datetime(
            df["accounting_date"], format="%m/%d/%Y", errors="coerce"
        )
        je = df[
            [
                "id",
                "company_id",
                "entry_id",
                "business_unit_id",
                "account_no",
                "amount",
                "accounting_date",
                "remarks",
            ]
        ].copy()
        je["entry_id"] = je["entry_id"].fillna(0).astype(int)
        je["account_no"] = (
            je["account_no"].fillna(0).astype(str).replace(r"\.0$", "", regex=True)
        )
        je["business_unit_id"] = (
            je["business_unit_id"]
            .fillna(0)
            .astype(str)
            .replace(r"\.0$", "", regex=True)
        )
        je["company_id"] = (
            je["company_id"].fillna(0).astype(str).replace(r"\.0$", "", regex=True)
        )
        je["amount"] = je["amount"].str.replace(",", "").fillna(0).astype(float)

        table = "journal_entry"
        Util.import_func(je, table, self.conn)

    def _import_journal_entry(self):
        try:
            raw = pd.read_csv(r"./source/JournalEntry.csv")
            logging.info("Journal Entry read from CSV.")
        except Exception as e:
            logging.exception(f"Error reading Journal Entry CSV: {e}")
            return

        df = raw.copy()
        df = df.reset_index(drop=True)
        df["id"] = df.index + 1
        df.columns = Util.sanitize_columns(df)

        df["accounting_date"] = pd.to_datetime(
            df["accounting_date"], format="%m/%d/%Y", errors="coerce"
        )
        df_split = df.copy()
        cols = [
            col for col in df_split.columns if "RAD" in col and "Description" not in col
        ]

        df_split[cols] = df_split[cols].applymap(
            lambda x: str(x) if not pd.isna(x) else np.nan
        )
        df_split["Combined"] = df_split[cols].apply(
            lambda row: "|".join([str(x) for x in row if not pd.isna(x)]), axis=1
        )
        df_split = df_split.assign(value=df_split["Combined"].str.split("|")).explode(
            "value"
        )
        df_result = df_split[["id", "value"]].reset_index(drop=True)
        df_result.replace("", float("NaN"), inplace=True)
        df_result.dropna(inplace=True)
        df_result.rename(
            columns={"value": "rad_id", "id": "journal_entry_id"}, inplace=True
        )

        table = "journal_entry_rad"
        Util.import_func(df_result, table, self.conn)

    def _import_rad(self):
        try:
            raw = pd.read_csv(r"./source/Rad.csv")
            logging.info("RAD data read from CSV.")
        except Exception as e:
            logging.exception(f"Error reading RAD CSV: {e}")
            return

        rad = raw.copy()
        rad.columns = Util.sanitize_columns(rad)
        rad = rad[["rad_type_id", "rad_id", "rad"]]

        table = "rad"
        Util.import_func(rad, table, self.conn)

    def _import_rad_type(self):
        try:
            raw = pd.read_csv(r"./source/Rad.csv")
            logging.info("RAD data read from CSV.")
        except Exception as e:
            logging.exception(f"Error reading RAD CSV: {e}")
            return

        rad = raw.copy()
        rad = Util.sanitize_columns(rad)
        rad = rad[["rad_type_id", "rad_type"]]
        rad = rad.drop_duplicates()

        table = "rad_type"
        Util.import_func(rad, table, self.conn)

    def _import_business_unit(self):
        try:
            raw = pd.read_csv(r"./source/BusinessUnit.csv")
            logging.info("Business Unit data read from CSV.")
        except Exception as e:
            logging.exception(f"Error reading Business Unit CSV: {e}")
            return

        df = raw.copy()
        df = df.reset_index(drop=True)
        df.columns = Util.sanitize_columns(df)
        df["id"] = df.index + 1
        df = df[
            [
                "id",
                "BusinessUnitid",
                "BusinessUnit",
                "company_id",
                "Company",
                "date_created",
            ]
        ]
        df["date_created"] = pd.to_datetime(
            df["date_created"], format="%m/%d/%Y"
        ).dt.strftime("%Y-%m-%d %H:%M:%S")

        table = "business_unit"
        Util.import_func(df, table, self.conn)

    def _import_budget_entry_admin_view(self):
        try:
            raw = pd.read_csv(r"./source/BudgetEntryAdminView.csv")
            logging.info("Budget Entry Admin View data read from CSV.")
        except Exception as e:
            logging.exception(f"Error reading Budget Entry Admin View CSV: {e}")
            return

        df = raw.copy()
        df = df.reset_index(drop=True)
        df.columns = df.columns.str.replace(" ", "")
        df["id"] = df.index + 1

        table = "budget_entry_admin_view"
        Util.import_func(df, table, self.conn)


from psycopg2.extras import execute_values
import pandas as pd
import logging


class Util:
    @staticmethod
    def import_func(df: pd.DataFrame, table: str, conn) -> None:
        try:
            cursor = conn.cursor()

            # Fetch columns from the target table
            cursor.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                (table,),
            )
            table_columns = {row[0] for row in cursor.fetchall()}
            df = df[[col for col in df.columns if col in table_columns]]
            df = df.replace({np.nan: None})
            df = df.replace({"": None})

            if df.empty:
                raise ValueError(
                    f"No matching columns to import into table {table}. Available columns: {table_columns}"
                )

            # Dynamically construct SQL INSERT statement
            columns = ", ".join([f"{col}" for col in df.columns])
            values_template = ", ".join(["%s"] * len(df.columns))
            insert_query = f"INSERT INTO {table} ({columns}) VALUES ({values_template})"

            # Convert DataFrame rows to a list of tuples
            rows = [tuple(row) for row in df.to_numpy()]

            if len(rows[0]) != len(df.columns):
                raise ValueError("Row length does not match number of columns in query")

            cursor.executemany(insert_query, rows)

            # Commit the transaction
            conn.commit()
            logging.info(f"Data imported into {table} successfully.")
        except Exception as error:
            logging.exception(f"Error importing data into {table}: {error}")
            raise error

    @staticmethod
    def execute_query(sql: str, conn) -> pd.DataFrame | None:
        try:
            df = pd.read_sql(sql, conn)
            logging.info(f"Data from query:\n{sql}\n successful.")
            return df
        except Exception as error:
            logging.exception(f"Error from query:\n{sql}\n {error}")
            raise error

    @staticmethod
    def sanitize_columns(df: pd.DataFrame) -> pd.DataFrame:
        df.columns = df.columns.str.replace(" ", "_").str.lower()
        df.columns = df.columns.str.replace("/", "")
        df.columns = df.columns.str.replace(".", "")
        return df


if __name__ == "__main__":
    try:
        db_manager = DBManager()
        conn = db_manager.connect()

        if conn:
            migration = Migration(conn=conn, schema_file="schema.sql")
            migration.delete_all_tables()
            migration.apply_schema()
            migration.import_all()
    except Exception as error:
        raise error
