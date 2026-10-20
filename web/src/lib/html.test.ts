import { describe, expect, it } from 'vitest';
import { escapeHtml } from './html';

describe('escapeHtml', () => {
  it('資料字串進 tooltip 前跳脫，事件屬性與標籤不會被解析', () => {
    expect(escapeHtml('<img src=x onerror="window.pwned=1">')).toBe('&lt;img src=x onerror=&quot;window.pwned=1&quot;&gt;');
    expect(escapeHtml("A & 'B'")).toBe('A &amp; &#39;B&#39;');
    expect(escapeHtml(null)).toBe(''); expect(escapeHtml(12)).toBe('12');
  });
});
