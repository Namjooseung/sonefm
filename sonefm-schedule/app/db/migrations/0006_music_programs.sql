-- 0006: 방송실시결과 보고서의 대중음악 프로그램 목록 (2026-11-15). 순서 = 보고서 4면 표 순서.
-- 기본값: 고정 편성(굿모닝·굿바이) + 이름이 '그 사이 음악', '963 음악쌀롱'인 프로그램. 설정 화면에서 고친다.
INSERT OR IGNORE INTO settings (id, key, value_json)
SELECT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
       'music_program_ids',
       (SELECT json_group_array(id) FROM (
          SELECT f.value AS id, f.key AS ord FROM settings s, json_each(s.value_json) f WHERE s.key = 'fixed_program_ids'
          UNION ALL
          SELECT p.id, 100 + (p.name = '963 음악쌀롱') FROM programs p
           WHERE p.name IN ('그 사이 음악', '963 음악쌀롱')
             AND p.id NOT IN (SELECT f.value FROM settings s, json_each(s.value_json) f WHERE s.key = 'fixed_program_ids')
          ORDER BY ord));
