/**
 * CSV text for the screener's "Export CSV" button. Pure and import-free so it
 * can be unit-tested with plain Node (frontend/tests/screener.test.mjs).
 *
 * Two things matter beyond joining with commas:
 *  - quoting: a cell with a comma, quote or line break is wrapped in quotes with
 *    inner quotes doubled (RFC 4180);
 *  - spreadsheet formulas: a text cell starting with = + - @ (or a tab / carriage
 *    return) would be run as a formula when the file is opened in Excel or
 *    Sheets, so it gets a leading apostrophe. Numbers are written as they are
 *    (a negative number is a number, not text, so it is not touched).
 */
export type CsvCell = string | number | null | undefined;

const FORMULA_START = /^[=+\-@\t\r]/;

export function csvCell(value: CsvCell): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '';
  const text = FORMULA_START.test(value) ? `'${value}` : value;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(headers: string[], rows: CsvCell[][]): string {
  return [headers, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
}
