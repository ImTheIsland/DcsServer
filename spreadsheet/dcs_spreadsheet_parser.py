import logging
import os
import re
from datetime import datetime

import pandas as pd
from openpyxl import load_workbook
from pandas import DataFrame

logger = logging.getLogger(__name__)

# Matches 1-2 non-alphanumeric characters only (expansion indicator in column 2)
_EXPANSION_INDICATOR_RE = re.compile(r'^[^a-zA-Z0-9]{1,2}$')

# Matches a replacement row: 1-2 non-alphanumeric chars, then whitespace, then values
_REPLACEMENT_ROW_RE = re.compile(r'^([^a-zA-Z0-9]{1,2})\s+(.+)$')


class DcsGsParser:
    """Parser for DCS Code data in google sheets. This normalizes the data and replaces all asterisks."""

    def __init__(self, excel_path: str | None = None):

        # Load the DCS Codes from the local Excel file
        if not excel_path:
            raise ValueError("Excel file path is required")

        if not os.path.exists(excel_path):
            raise FileNotFoundError(f"Excel file not found at {excel_path}")

        self.excel_path = excel_path
        self.initial_df = pd.read_excel(excel_path, sheet_name=0, header=0)

        self.result_df = None

        self._blocks: list[DataFrame] = []

        self._cleaned_df = self._clear_header_rows(self.initial_df)

        self._parse_it_all()

    def _clear_header_rows(self, df) -> DataFrame:
        # cast the first column as a string and look for rows that contain the words "DCS Codes" and replace them with an empty row
        # Find rows containing "DCS Codes" in the first column and replace them with empty rows
        mask = df.iloc[:, 0].str.contains("DCS Codes", na=False)
        df.loc[mask] = pd.NA

        return df

    def _parse_it_all(self):

        narrow_df = self._wide_to_narrow(self._cleaned_df)
        print(f"Narrow dataframe has {len(narrow_df)} rows and {len(narrow_df.columns)} columns")

        # if 2 or more consecutive rows found with all values being "nan", replace them with a SINGLE row with nan values
        result = self._reduce_multiple_empties_rows_to_one(narrow_df)

        blocks = self._parse_into_blocks(result)

        # Create a new dataframe to store the parsed data. Set the columns to ["D", "C", "S", "Description", "Type"]
        final_df = pd.DataFrame()

        for block in blocks:
            block_df = self._parse_block(block)

            final_df = pd.concat([final_df, block_df], ignore_index=True)

        # rename the columns
        final_df.columns = ["D", "C", "S", "Description", "Type", "DCS"]

        # reorder the columns
        final_df = final_df[["DCS", "D", "C", "S", "Type", "Description"]]

        # strip the DCS column
        final_df["DCS"] = final_df["DCS"].str.strip()

        # sort by DCS
        final_df = final_df.sort_values(by="DCS")

        self.result_df = final_df

    def _wide_to_narrow(self, df) -> DataFrame:
        """
            Parse the initial dataframe into a "narrow" dataframe based on certain criteria:

            1) The data is organized into blocks of 4 columns and then a spacer column.
            2) There will be multiple blocks of data across the x-axis of the dataframe. So column 0-3, 5-8, 10-13, etc. will have blocks of data.

            This will move all the data into a single vertical block of 4 columns.

            Returns:
                DataFrame: List of parsed blocks
        """
        # Create an empty list to store the blocks
        blocks = []

        # Get total number of columns
        total_cols = len(df.columns)
        print(f"Total columns: {total_cols}")

        # Iterate through the dataframe in blocks of 4 columns (plus spacer)
        # Pattern: cols 0-3 (data), col 4 (spacer), cols 5-8 (data), col 9 (spacer), etc.
        for start_col in range(0, total_cols, 5):
            # Check if we have enough columns remaining for a block
            if start_col + 4 <= total_cols:
                # Extract the 4 columns block
                block = df.iloc[:, start_col:start_col + 4].copy()

                # Reset column names to 0, 1, 2, 3 for consistency
                block.columns = range(4)
                # Add a blank row at the end of the block this stops the bottom block from being concatenated with the next block at the top
                block = pd.concat([block, pd.DataFrame([[None] * 4], columns=range(4))], ignore_index=True)

                blocks.append(block)
                print(f"Added block from columns {start_col} to {start_col + 3}")

        # Concatenate all blocks vertically (axis=0) into a single narrow dataframe
        result = None
        if len(blocks) > 0:
            result = pd.concat(blocks, axis=0, ignore_index=True)
            print(f"Created narrow dataframe with {len(result)} rows and {len(result.columns)} columns")

        return result

    def _reduce_multiple_empties_rows_to_one(self, df) -> DataFrame:
        """
        If 2 or more consecutive rows found with all values being "nan",
        replace them with a SINGLE row with nan values.

        Args:
            df: DataFrame to process

        Returns:
            DataFrame with consecutive empty rows reduced to single empty rows
        """
        if df is None or df.empty:
            return df

        # Create a boolean mask for completely empty rows (all NaN)
        empty_rows_mask = df.isna().all(axis=1)

        # Track which rows to keep
        rows_to_keep = []

        in_empty_sequence = False

        for idx in range(len(df)):
            is_empty = empty_rows_mask.iloc[idx]

            if is_empty:
                if not in_empty_sequence:
                    # First empty row in a sequence - keep it
                    rows_to_keep.append(idx)
                    in_empty_sequence = True
                # else: skip subsequent empty rows in the sequence
            else:
                # Non-empty row - always keep it
                rows_to_keep.append(idx)
                in_empty_sequence = False

        # Return the filtered dataframe
        result = df.iloc[rows_to_keep].reset_index(drop=True)
        return result

    def _parse_into_blocks(self, df) -> list[DataFrame]:
        """
            Parse the initial dataframe into blocks based on certain criteria:

            1) The data is organized into blocks of 4 columns and then a spacer column.
            2) There will be multiple blocks of data across the x-axis of the dataframe. So column 0-3, 5-9, 11-15, etc. will have blocks of data.
            3) The first step is to move all the data into a single vertical block of 4 columns.

            Returns:
                list[DataFrame]: List of parsed blocks
        """

        # break up the dataframe into block. Empty rows define the block boundaries.
        blocks = []
        current_block = []
        for idx, row in df.iterrows():
            if row.isna().all():
                if current_block:
                    blocks.append(pd.DataFrame(current_block))
                    current_block = []
            else:
                current_block.append(row)

        print(f"Created {len(blocks)} blocks")
        return blocks

    def _parse_block(self, block: DataFrame) -> DataFrame:
        """
        Parse a block of data using the following criteria:
        1) Title: the block will start with a title in the first column. Remove it from the block and store it in the "Type" column
        2) Replacements: Scan consecutive rows from the bottom of the block whose first column matches
           the pattern "<symbol> val1, val2, ..." where <symbol> is 1-2 non-alphanumeric characters.
           Each such row is removed from the block and stored in a replacement dict keyed by the symbol.
        3) For the first 4 columns:
            a) If any rows have column 0 empty, populate it with the value from the previous column 0
            b) If any rows have column 1 empty, populate it with the value from the previous column 1 (if any)
            c) If any rows have column 3 empty, populate it with the value from the previous column 3 (if any)
            d) if column 2 contains a 1-2 non-alphanumeric expansion indicator:
                i) look up the indicator in the replacement dict
                ii) if found, insert one row per replacement value (columns 0,1,3 copied)
                iii) if not found, log a warning and keep the indicator value as-is

        Example 1:
            "Climbing", "", "", ""
            "CLI", "CAM", "", "Cams"
            "", "", "OFS", ""
        Becomes:
            "CLI", "CAM", "", "Cams", "Climbing"
            "CLI", "CAM", "OFS", "Cams", "Climbing"

        Example 2:
            "Climbing", "", "", ""
            "CLI", "HNS", "*", "Harness"
            "", "", "OTH", ""
            * MEN, WMN, KID
        Becomes:
            "CLI", "HNS", "MEN", "Harness", "Climbing"
            "CLI", "HNS", "WMN", "Harness", "Climbing"
            "CLI", "HNS", "KID", "Harness", "Climbing"
            "CLI", "HNS", "OTH", "Harness", "Climbing"

        Example 3 (multiple expansion symbols):
            "Clothing", "", "", ""
            "CLO", "HDY", "**", "Hoodies"
            "CLO", "SST", "*",  "Short Sleeve Tee"
            * MEN, WMN, UNI
            ** MEN, WMN, GRL, BOY, YTH
        Becomes:
            "CLO", "HDY", "MEN", "Hoodies",          "Clothing"
            "CLO", "HDY", "WMN", "Hoodies",          "Clothing"
            "CLO", "HDY", "GRL", "Hoodies",          "Clothing"
            "CLO", "HDY", "BOY", "Hoodies",          "Clothing"
            "CLO", "HDY", "YTH", "Hoodies",          "Clothing"
            "CLO", "SST", "MEN", "Short Sleeve Tee", "Clothing"
            "CLO", "SST", "WMN", "Short Sleeve Tee", "Clothing"
            "CLO", "SST", "UNI", "Short Sleeve Tee", "Clothing"
        """
        if block is None or block.empty:
            return block

        # Make a copy to avoid modifying the original
        df = block.copy()

        # Remove completely blank rows
        df = df.dropna(how='all').reset_index(drop=True)

        if df.empty:
            return df

        # Step 1: Extract title from first row, first column
        title = str(df.iloc[0, 0]) if pd.notna(df.iloc[0, 0]) else ""
        df = df.iloc[1:].reset_index(drop=True)  # Remove title row

        if df.empty:
            return df

        # Step 2: Collect all replacement rows from the bottom of the block.
        # A replacement row has column 0 matching "<symbol> val1, val2, ..." where
        # <symbol> is 1-2 non-alphanumeric characters.
        replacements: dict[str, list[str]] = {}
        rows_to_drop = []
        for i in range(len(df) - 1, -1, -1):
            first_col = str(df.iloc[i, 0]) if pd.notna(df.iloc[i, 0]) else ""
            first_col = first_col.strip()
            m = _REPLACEMENT_ROW_RE.match(first_col)
            if m:
                symbol = m.group(1)
                values = [v.strip() for v in m.group(2).split(',') if v.strip()]
                replacements[symbol] = values
                rows_to_drop.append(i)
            else:
                break  # Stop at the first non-replacement row from the bottom

        if rows_to_drop:
            df = df.drop(df.index[rows_to_drop]).reset_index(drop=True)

        if df.empty:
            return df

        # Step 3a, 3b, 3c: Forward fill columns 0, 1, and 3
        for col_idx in [0, 1, 3]:
            if col_idx < len(df.columns):
                # Forward fill: replace NaN/empty with previous non-empty value
                df.iloc[:, col_idx] = df.iloc[:, col_idx].replace('', pd.NA).ffill()

        # Step 3d: Handle expansion indicators in column 2.
        # An expansion indicator is 1-2 non-alphanumeric characters (e.g. "*", "**", "!", "%").
        expanded_rows = []

        for idx, row in df.iterrows():
            col2_value = str(row.iloc[2]) if pd.notna(row.iloc[2]) else ""
            col2_value = col2_value.strip()

            if _EXPANSION_INDICATOR_RE.match(col2_value):
                if col2_value not in replacements:
                    logger.warning(
                        f"Expansion indicator '{col2_value}' found in column 2 at row {idx} "
                        f"but no replacement list is defined for this symbol in the block. Keeping as-is."
                    )
                    expanded_rows.append(row)
                else:
                    # Create one row per replacement value
                    for replacement in replacements[col2_value]:
                        new_row = row.copy()
                        new_row.iloc[2] = replacement
                        expanded_rows.append(new_row)
            else:
                expanded_rows.append(row)

        # Reconstruct dataframe from expanded rows
        if expanded_rows:
            df = pd.DataFrame(expanded_rows).reset_index(drop=True)

        # Add the "Type" column with the title
        df['Type'] = title

        # Merge the first 3 columns into a new "DCS" column
        # Each value should be 3 characters wide (padded with spaces if needed)
        def pad_to_3_chars(val):
            """Pad value to 3 characters, treating NaN as empty string"""
            if pd.isna(val):
                return "   "
            str_val = str(val)
            return str_val.ljust(3)[:3]  # Left justify and pad to 3 chars, then truncate if longer

        # replace NaN with empty string
        df = df.fillna('')

        # Apply padding to first 3 columns and concatenate
        df['DCS'] = (df.iloc[:, 0].apply(pad_to_3_chars) +
                     df.iloc[:, 1].apply(pad_to_3_chars) +
                     df.iloc[:, 2].apply(pad_to_3_chars))

        return df

    def write_normalized_sheet(self):
        """Write result_df to a 'Normalized' worksheet in the source Excel file.

        Row 1: Generated: <timestamp>
        Row 2: Column headers
        Row 3+: Data
        """
        if self.result_df is None or self.result_df.empty:
            logger.warning("No data to write — result_df is empty.")
            return


        wb = load_workbook(self.excel_path)

        # Replace the 'Normalized' sheet if it already exists
        if 'Normalized' in wb.sheetnames:
            del wb['Normalized']
        ws = wb.create_sheet('Normalized')

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        headers = list(self.result_df.columns)
        data_rows = self.result_df.fillna("").astype(str).values.tolist()

        ws['A1'] = f"Generated: {timestamp}"

        for col, header in enumerate(headers, 1):
            ws.cell(row=2, column=col, value=header)

        for row_idx, row in enumerate(data_rows, 3):
            for col_idx, value in enumerate(row, 1):
                ws.cell(row=row_idx, column=col_idx, value=value)

        wb.save(excel_path)
        print(f"Wrote {len(data_rows)} rows to 'Normalized' sheet in {excel_path}.")

    def write_keywords_sheet(self):
        """Manage the 'Keywords' worksheet in the source Excel file.

        If absent: create it with columns DCS, Type, Description, Keywords (blank).
        If present: append any DCS codes not already in the sheet.
        """
        if self.result_df is None or self.result_df.empty:
            logger.warning("No data to write — result_df is empty.")
            return

        wb = load_workbook(self.excel_path)

        # First column of result_df is always the combined DCS code
        dcs_col = self.result_df.columns[0]
        source = self.result_df[[dcs_col, 'Type', 'Description']].fillna('').astype(str)

        if 'Keywords' not in wb.sheetnames:
            ws = wb.create_sheet('Keywords')
            ws.append(['DCS', 'Type', 'Description', 'Keywords'])
            for _, row in source.iterrows():
                ws.append([row[dcs_col], row['Type'], row['Description'], ''])
            print(f"Created 'Keywords' sheet with {len(source)} rows.")
        else:
            ws = wb['Keywords']
            existing_dcs = {
                str(ws.cell(row=r, column=1).value).strip()
                for r in range(2, ws.max_row + 1)
                if ws.cell(row=r, column=1).value is not None
            }
            new_rows = source[~source[dcs_col].isin(existing_dcs)]
            for _, row in new_rows.iterrows():
                ws.append([row[dcs_col], row['Type'], row['Description'], ''])
            print(f"Appended {len(new_rows)} new DCS codes to 'Keywords' sheet.")

        wb.save(excel_path)


if __name__ == "__main__":
    excel_path = r'C:\Shared Drive\GearHeads\Departments\DCS List.xlsx'
    parser = DcsGsParser(excel_path)

    df = parser.result_df
    # print(df.to_string())
    print(df.head(10).to_string())
    print(df.tail(10).to_string())

    # Write normalized data back to the source spreadsheet
    parser.write_normalized_sheet()
    parser.write_keywords_sheet()
