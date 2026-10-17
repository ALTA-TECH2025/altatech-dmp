import { describe, expect, it } from 'vitest';
import { candidates } from './fetch';

describe('demo fetch 候選鍵', () => {
  it('動態 id 換成 *，字面子資源保留', () => {
    expect(candidates('GET', '/v1/meta/events/bond_order', '')).toContain('GET /v1/meta/events/*');
    expect(candidates('GET', '/v1/segments/seg_1/versions/latest/members', '?page_size=50')).toContain('GET /v1/segments/*/versions/latest/members?page_size=50');
    expect(candidates('POST', '/v1/dashboards/d_1/cards/c_2/data', '')).toContain('POST /v1/dashboards/*/cards/*/data');
    expect(candidates('GET', '/v1/semantic/catalog', '')[0]).toBe('GET /v1/semantic/catalog');
  });
});
