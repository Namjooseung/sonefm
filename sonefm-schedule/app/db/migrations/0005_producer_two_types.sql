-- 0005: 제작 주체를 자체/외부 둘로 (2026-11-15 사용자 결정). '타공동체라디오'는 외부로 합친다.
-- 0001의 CHECK 제약은 테이블을 다시 만들지 않으려고 그대로 둔다. 새 값은 domain/programs.py에서 막는다.
UPDATE programs SET producer_type = '외부', updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
 WHERE producer_type = '타공동체라디오';
