Employee Attendance and Analytics API

Welcome to the Employee Attendance and Analytics API project. This project is a RESTful API built using FastAPI and MongoDB to help manage employee records, track daily attendance punch ins and punch outs, and calculate attendance analytics.

Prerequisites
To run this application, you need Python 3 10 or newer and MongoDB 6 0 or newer running on your machine.

Quick Start
First, install the required packages using pip install requirements txt.
Second, set your database environment variables for MONGO URI and MONGO DB.
Third, load the sample dataset by running python sample seed py.
Fourth, start the server by running uvicorn app main app port 8000 reload.

Key Endpoints
The health endpoint GET health checks server and database status.
The employees endpoint POST employees registers a new employee.
The list employees endpoint GET employees lists employees with pagination.
The punch in endpoint POST attendance punch in records punch in time.
The punch out endpoint POST attendance punch out records punch out time.
The regularization endpoint PATCH attendance emp code date updates attendance records.
The employee monthly analytics endpoint GET analytics employees emp code monthly calculates monthly stats.
The department summary analytics endpoint GET analytics departments summary gives department totals.
The late leaderboard endpoint GET analytics leaderboard late ranks late employees.
The department trend endpoint GET analytics departments department trend shows daily trends.

Project Structure
The main py file in the app folder contains all the API logic.
The sample seed py file seeds sample data into MongoDB.
The openapi yaml file contains the API specification.
The review md file contains the code review report.
