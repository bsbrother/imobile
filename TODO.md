# TODO

- 2025.10.6
  Modify utils/downloader_by_drissionpage.py to use LLM, not use drissionpage.
  The apk filename format always as yyz_n1.n2.n3.*_gtja.apk. n1, n2, n3 are 1-2 digits, ignore any other digits or characters after n3, end with _gtja.apk.

- 2025.10.7
  Use class Tablex(...) and Tablex.f1, so can be reflex migrate.
  Now is directory changed by db/imobile.sql, db/migrations/add_realtime_fields.sql, and reload data: python app_guotai.py
