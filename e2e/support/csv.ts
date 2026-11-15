import { readFile } from 'node:fs/promises';
import type { Download } from '@playwright/test';

/** Minimal RFC 4180 parser: quoted fields, doubled quotes, embedded CR/LF, CRLF record endings. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') { field += '"'; i += 1; }
      else if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"') quoted = true;
    else if (char === ',') { row.push(field); field = ''; }
    else if (char === '\r' && text[i + 1] === '\n') { row.push(field); rows.push(row); row = []; field = ''; i += 1; }
    else field += char;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows;
}

export interface DownloadedCsv {
  filename: string;
  bytes: Buffer;
  text: string;
  header: string[];
  rows: Record<string, string>[];
}

export async function readCsvDownload(download: Download): Promise<DownloadedCsv> {
  const bytes = await readFile(await download.path());
  const text = bytes.toString('utf8');
  const [header, ...records] = parseCsv(text.replace(/^\uFEFF/, ''));
  return {
    filename: download.suggestedFilename(),
    bytes,
    text,
    header,
    rows: records.map(record => Object.fromEntries(header.map((name, i) => [name, record[i]]))),
  };
}
