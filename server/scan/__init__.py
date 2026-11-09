"""Daily review scan: a reviewer uploads one supporter's resumes ZIP and
snapshots ZIP for one day; the server matches them to the tracker records,
runs simple automatic checks, and writes a text report. People judge the
content; this package only finds duplicates, missing items and wrong records.

Modules:

* ``names``     - company-name and job-link matching helpers
* ``options``   - the reviewer's scan options and their defaults
* ``archive``   - safe ZIP checking and unpacking
* ``documents`` - reading resume and cover letter files
* ``captures``  - reading SingleFile captures of application forms
* ``checks``    - matching files to tracker records and the checks themselves
* ``report``    - the plain-text report
* ``worker``    - the background thread that runs one scan at a time
"""
