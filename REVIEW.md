Review Report

Hi, While going through the starter main.py file in the app folder, I found seven bugs and fixed them. Here are the details of what went wrong, how I noticed the issues during testing, and how I fixed them.

First issue in health endpoint
The health check endpoint was returning ok even when MongoDB was down. When I turned off Mongo and hit the endpoint, it still gave 200 instead of 503. I fixed this by adding a database ping inside a try block so it returns 503 if Mongo is down.

Second issue in create employee endpoint
It was using a manual lookup to check duplicate employees instead of a database index, causing race conditions. Also, created at returned a raw date string instead of epoch milliseconds. When I sent parallel requests with the same employee code, it allowed duplicates. I fixed it by creating a unique index on employee code at startup, catching duplicate key error for 409 status, and converting created at to epoch milliseconds.

Third issue in list employees endpoint
The skip logic was page multiplied by page size, which skipped the first page completely. Also, total count ignored department filter, and results were unsorted. When calling page 1, it skipped the first 20 employees. I changed skip to page minus one times page size, passed the filter to count documents, and sorted by employee code.

Fourth issue in punch in endpoint
It crashed with a 500 error for unknown employee code and allowed duplicate punch ins on the same date. Also, overnight shifts saved under the wrong date and invalid statuses were accepted. Punching in with a fake employee code crashed the server. I added a 404 check for invalid employee, created a compound index on employee code and date for 409 status, implemented IST date logic, and validated status strings.

Fifth issue in list attendance endpoint
It loaded all documents into memory using list before sorting, missed secondary sort, and lacked date range validation. The API became very slow on large data and setting from date after to date returned 200 instead of 422. I moved sorting and pagination into database queries and added date range validation for 422 error.

Sixth issue in late minutes calculation
It compared UTC timestamps with local time strings directly and messed up late calculations. A punch at 10 15 AM for a 09 30 AM shift gave wrong numbers because UTC is off by 5.5 hours from IST. I converted UTC timestamps to IST, checked if punch in was strictly more than 10 minutes late, and calculated total late minutes from shift start.

Seventh issue in work hours and overtime calculation
Work hours had float precision issues and overtime failed for overnight shifts crossing midnight. Work hours showed values like 8.000000000000002. Overtime calculation broke after midnight. I used Decimal with half up rounding for 2 decimal places and fixed shift end IST calculation across midnight.

Other things I checked
I checked if keeping all code in a single file app main py was an issue, but the project instructions explicitly requested a single file structure. I also checked the error response format and decided using detail in responses is standard for FastAPI.
