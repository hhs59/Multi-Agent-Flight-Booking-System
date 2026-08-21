# Multi-Agent Flight Booking System: Final Project Report

**Degree Program:** Bachelor of Science in Software Engineering  
**Document Type:** Final University Project Technical Report  
**System Implementation Basis:** Backend source code (`src/`), Frontend source code (`frontend/src/`), Database migrations (`alembic/`), Auth0 OIDC configuration (`auth0/`), and Automated Test Suites  
**Target Document Volume:** 30–40 Pages (Formatted Standard University Layout)  
**Primary Stack:** Python >= 3.11 (Python 3.11, 3.12, 3.13, 3.14+), FastAPI, LangGraph, Pydantic v2, SQLAlchemy 2, PostgreSQL, React 19, TypeScript, Vite, TanStack Query  

---

## Executive Abstract

This final university project report documents the engineering, system architecture, implementation, and evaluation of an AI-assisted flight search and booking system. The primary goal of the project is to allow travelers to discover travel destinations, search for flight offers, receive destination advice, set price alerts, and complete sandbox booking orders using flexible natural language dialogue, while maintaining strict deterministic backend control over travel facts, pricing, and transactions.

Natural language interaction is processed by an external Large Language Model (LLM) integrated via an OpenAI-compatible API adapter. The model extracts structured semantic parameters from user messages without being permitted to generate flight numbers, airport codes, ticket prices, or booking references. Deterministic Python backend services resolve locations against an airport catalog, parse relative dates based on server timezone (`Asia/Ho_Chi_Minh`), validate passenger counts and budget constraints, query the Duffel flight API, manage PostgreSQL database persistence, and enforce booking workflow safety rules.

The system is implemented as a multi-tier application comprising a Python FastAPI REST service, a React 19 / TypeScript single-page web interface, a PostgreSQL relational database with 24 model tables and 16 Alembic migrations, and an Auth0 OpenID Connect (OIDC) identity integration using secure HTTP-only session cookies with CSRF protection and AES-256-GCM authenticated encryption (AEAD) for sensitive traveler personal data. Software verification confirmed 605 passed test executions, 16 skipped tests, and 2 deselected tests in the backend suite, alongside 100 passed tests across 19 test files in the frontend suite.

**Keywords:** Conversational AI, Task-Oriented Dialogue, LangGraph, Duffel Flight API, FastAPI, React 19, PostgreSQL, OpenID Connect, Software Integration.

---

## TABLE OF CONTENTS

- [INTRODUCTION / PROBLEM STATEMENT](#introduction--problem-statement)
  - [Background](#background)
  - [Problem Statement](#problem-statement)
  - [Project Objectives](#project-objectives)
  - [Scope and Limitations](#scope-and-limitations)
  - [Report Organization](#report-organization)
- [CHAPTER 1. THEORETICAL FOUNDATIONS](#chapter-1-theoretical-foundations)
  - [1.1 Conversational AI](#11-conversational-ai)
  - [1.2 Large Language Models](#12-large-language-models)
  - [1.3 Prompt Engineering](#13-prompt-engineering)
  - [1.4 Structured Output](#14-structured-output)
  - [1.5 Multi-Agent Systems](#15-multi-agent-systems)
  - [1.6 LangGraph](#16-langgraph)
  - [1.7 REST APIs](#17-rest-apis)
  - [1.8 Database](#18-database)
  - [1.9 Authentication and Security](#19-authentication-and-security)
  - [1.10 Technologies Used](#110-technologies-used)
- [CHAPTER 2. SYSTEM ANALYSIS AND DESIGN](#chapter-2-system-analysis-and-design)
  - [2.1 System Overview](#21-system-overview)
  - [2.2 Stakeholders](#22-stakeholders)
  - [2.3 Functional Requirements](#23-functional-requirements)
  - [2.4 Non-functional Requirements](#24-non-functional-requirements)
  - [2.5 Actors](#25-actors)
  - [2.6 Use Cases](#26-use-cases)
  - [2.7 System Architecture](#27-system-architecture)
  - [2.8 Database Design](#28-database-design)
  - [2.9 Multi-Agent Design](#29-multi-agent-design)
  - [2.10 Conversation Management](#210-conversation-management)
  - [2.11 Flight Search](#211-flight-search)
  - [2.12 Recommendation](#212-recommendation)
  - [2.13 Weather](#213-weather)
  - [2.14 Booking Workflow](#214-booking-workflow)
  - [2.15 Authentication](#215-authentication)
  - [2.16 Security](#216-security)
  - [2.17 Error Handling](#217-error-handling)
  - [2.18 Idempotency](#218-idempotency)
- [CHAPTER 3. MODEL TRAINING AND SYSTEM INTEGRATION](#chapter-3-model-training-and-system-integration)
  - [3.1 AI Model Approach](#31-ai-model-approach)
  - [3.2 Model Training Status](#32-model-training-status)
  - [3.3 LLM Configuration](#33-llm-configuration)
  - [3.4 Prompt Design](#34-prompt-design)
  - [3.5 Structured Output](#35-structured-output)
  - [3.6 Multi-Agent Orchestration](#36-multi-agent-orchestration)
  - [3.7 Semantic Processing](#37-semantic-processing)
  - [3.8 Deterministic Validation](#38-deterministic-validation)
  - [3.9 Flight API Integration](#39-flight-api-integration)
  - [3.10 Weather Integration](#310-weather-integration)
  - [3.11 Database Integration](#311-database-integration)
  - [3.12 Authentication Integration](#312-authentication-integration)
  - [3.13 Booking Integration](#313-booking-integration)
  - [3.14 Error Handling](#314-error-handling)
  - [3.15 End-to-End Data Flow](#315-end-to-end-data-flow)
- [CHAPTER 4. RESULTS, TESTING AND SYSTEM EVALUATION](#chapter-4-results-testing-and-system-evaluation)
  - [4.1 Testing Strategy](#41-testing-strategy)
  - [4.2 Test Environment](#42-test-environment)
  - [4.3 Backend Testing](#43-backend-testing)
  - [4.4 Frontend Testing](#44-frontend-testing)
  - [4.5 Unit Testing](#45-unit-testing)
  - [4.6 Integration Testing](#46-integration-testing)
  - [4.7 Booking Testing](#47-booking-testing)
  - [4.8 Security Testing](#48-security-testing)
  - [4.9 LLM-related Testing](#49-llm-related-testing)
  - [4.10 Test Results](#410-test-results)
  - [4.11 Sample User Verification Scenarios](#411-sample-user-verification-scenarios)
  - [4.12 Evaluation Against Objectives](#412-evaluation-against-objectives)
  - [4.13 Limitations](#413-limitations)
  - [4.14 Threats to Validity](#414-threats-to-validity)
- [CONCLUSION AND FUTURE DEVELOPMENT](#conclusion-and-future-development)
  - [Conclusion](#conclusion)
  - [Future Development](#future-development)
- [REFERENCES](#references)
- [APPENDICES](#appendices)
  - [Appendix A: Source Code & Implementation Evidence Mapping](#appendix-a-source-code--implementation-evidence-mapping)
  - [Appendix B: Database Schema & Entity Quick Reference (24 Model Classes)](#appendix-b-database-schema--entity-quick-reference-24-model-classes)
  - [Appendix C: System REST API Endpoints Reference](#appendix-c-system-rest-api-endpoints-reference)
  - [Appendix D: System Configuration & Environment Variables](#appendix-d-system-configuration--environment-variables)
  - [Appendix E: System Verification & Execution Commands](#appendix-e-system-verification--execution-commands)
  - [Appendix F: LLM Planner Output Schema Structure](#appendix-f-llm-planner-output-schema-structure)

---

# INTRODUCTION / PROBLEM STATEMENT

## Background

Flight planning usually requires travelers to fill out rigid search forms with explicit parameters: origin airport code, destination airport code, departure date, return date, passenger composition, cabin class, and price limits. Traditional search forms work well when users already know these exact values. However, they provide limited support when users have flexible or incomplete travel plans, such as *"I have 5 million VND, where can I travel next week?"* or *"Find me a cheap flight from Hanoi to Singapore next month."*

A conversational assistant allows travelers to express their goals in flexible natural language, ask follow-up questions, and refine search criteria step by step. However, building a travel assistant introduces technical challenges. Flight availability, ticket prices, IATA airport codes, offer validity periods, traveler records, and booking transactions must be completely accurate. Language models can understand natural language well, but they can also hallucinate facts, make up non-existent flight numbers, or invent fake prices. Therefore, a flight booking system cannot rely solely on an LLM to manage travel data or execute transactions.

To solve this problem, this project uses a hybrid software architecture. Natural language understanding is handled by a language model, while domain validation, location resolution, flight offer searches, state management, database storage, and booking workflows are handled entirely by deterministic backend code.

## Problem Statement

This project addresses the following primary engineering problem:

> How to design and implement a conversational flight search and booking system that accurately interprets flexible natural language requests, while ensuring that all flight data, location resolutions, date calculations, price comparisons, and booking state transitions remain under strict deterministic backend control.

To solve this problem effectively, the system must address five specific technical challenges:

1. **Natural Language Ambiguity:** Users may mention city names instead of airport codes, specify relative dates like *"next Friday"*, make spelling mistakes, or mix English and Vietnamese.
2. **External Data Volatility:** Flight offers expire quickly, prices change dynamically, and external API providers may be temporarily unavailable.
3. **Multi-Turn Continuity:** Follow-up requests like *"show me the cheapest one"* or *"book option 2"* depend on results returned in earlier conversation turns.
4. **Transaction Safety:** Flight searches are read-only operations, but creating a booking changes external provider states. Booking requires valid traveler profiles, fresh price quotes, explicit user confirmation, and idempotency protection to prevent duplicate orders.
5. **Operational Resilience:** Failures in optional third-party services (such as weather forecasts or place recommendations) must not break core flight search results.

## Project Objectives

The main objectives of this project are:

1. Implement a web-based conversational interface for flight discovery, search, recommendations, price tracking (watches), user profile management, and booking workflows.
2. Integrate an LLM provider using structured output validation to extract user intent without allowing the model to generate flight data or modify system state directly.
3. Build a location resolution service that maps user location input (including misspelled or diacritic-free text) to valid IATA airport codes.
4. Implement a server-side date resolver that calculates exact date windows from relative expressions based on a fixed travel timezone.
5. Integrate an external flight API (Duffel) to retrieve live flight offers and create sandbox bookings.
6. Provide destination inspiration by matching verified flight fares against user budget constraints.
7. Support optional destination weather forecasts (OpenWeather) and curated place recommendations without letting external API errors block flight search results.
8. Store conversation history, flight searches, user preferences, quotes, and booking records reliably using PostgreSQL.
9. Implement authentication via OpenID Connect (OIDC), secure session management, CSRF token validation, and AES-256-GCM authenticated encryption (AEAD) for sensitive traveler data.
10. Verify system behavior using automated backend and frontend test suites.

## Scope and Limitations

### Implemented Scope
- **Backend:** Python >= 3.11 application using FastAPI, Pydantic, SQLAlchemy 2, Alembic migrations, and LangGraph orchestration.
- **Frontend:** Single-page web application built with React 19, TypeScript, Vite, TanStack Query, and React Router v7.
- **LLM Integration:** OpenAI-compatible API adapter configured for remote LLM execution (such as DeepSeek models).
- **Flight API:** Duffel flight offer search, offer repricing/quote preparation, and sandbox order creation.
- **Enrichment APIs:** OpenWeather API integration for destination weather forecasts, and curated place recommendation data.
- **Security & Storage:** Auth0/Keycloak-compatible OIDC authentication, secure cookie session storage, CSRF token validation, AES-256-GCM authenticated encryption (AEAD) for traveler PII, and 24 database model tables in PostgreSQL.

### Explicit Project Limitations
- The project does not train or fine-tune custom AI models.
- The system does not issue real production tickets or process live monetary credit card payments (order creation is restricted to Duffel sandbox environments).
- Budget filtering applies strictly to airfare; it does not compute complete trip expenses such as hotel accommodation, food, or local transport.
- Benchmark accuracy for natural language intent classification or typo correction was not measured against a formal public dataset.
- Automated tests verify software contracts and API integrations using mock fixtures and sandbox endpoints; they do not measure live model accuracy or human user satisfaction.

## Report Organization

The remainder of this report is organized into four main chapters and supporting sections:
- **CHAPTER 1 (Theoretical Foundations)** explains conversational AI, task-oriented dialogue systems, LLMs, prompt engineering, structured output validation, multi-agent systems, LangGraph, REST APIs, databases, authentication, and system security.
- **CHAPTER 2 (System Analysis and Design)** presents stakeholders, functional/non-functional requirements, system actors, use cases, architecture diagrams, component decomposition, database schema, multi-agent design, and workflow designs.
- **CHAPTER 3 (Model Training and System Integration)** details the AI integration approach, explicit model training status, LLM configuration, prompt design, structured schemas, multi-agent orchestration, and integrations with Duffel, OpenWeather, PostgreSQL, and Auth0.
- **CHAPTER 4 (Results, Testing and System Evaluation)** documents test strategies, test environments, unit/integration/booking/security test results, sample user verification scenarios, objective evaluations, explicit limitations, and threats to validity.
- **CONCLUSION AND FUTURE DEVELOPMENT** summarizes project contributions and outlines future software enhancements.
- **REFERENCES & APPENDICES** lists official technical references and provides 6 detailed quick-reference appendices.

---

# CHAPTER 1. THEORETICAL FOUNDATIONS

## 1.1 Conversational AI

Conversational AI focuses on building software that enables human-like interactions between computers and users through text or speech. In travel software, conversational interfaces allow users to explain complex constraints—such as flexible dates, destination preferences, and budget limits—in natural language rather than through multiple dropdown menus.

Traditional rule-based dialogue interfaces rely on strict keyword matching and fixed forms. They often break when users phrase requests in unexpected ways or omit required fields. Modern conversational AI uses statistical and neural language models to handle language variations while maintaining context across multi-turn interactions.

## 1.2 Large Language Models

Large Language Models (LLMs) are deep learning models trained on broad textual datasets. They excel at text interpretation, entity extraction, sentiment analysis, and multi-turn dialogue management. In this application, the LLM is used as an intent interpreter.

The LLM processes user messages, identifies requested travel actions, and extracts key semantic parameters such as cities, dates, and budgets. The LLM is configured via system instructions and JSON response constraints to ensure it acts as an interpreter rather than a factual data generator. This design reduces the risk of the LLM generating unsupported airport codes, flight availability, or ticket prices.

## 1.3 Prompt Engineering

Prompt engineering is the practice of structuring instructions and input context provided to an LLM to guide its output. System instructions define the model's persona, constraints, available tools, and expected output format.

In this project, prompts explicitly instruct the LLM to return structured JSON adhering to strict schemas. Prompts forbid the model from generating imaginary IATA codes, flight fares, or booking confirmation references, reinforcing the boundary between language understanding and domain execution.

## 1.4 Structured Output

Unstructured text output from a language model is hard to parse safely in web applications. To enforce reliability, the system uses structured output validation backed by Pydantic schemas.

When sending requests to the LLM, the backend specifies a strict JSON schema for the response. The model returns structured fields including command type, conversation language, dialogue act, and semantic updates. If the model returns malformed JSON or violates the required schema, the backend validation layer catches the exception and safely falls back to a controlled clarification prompt.

## 1.5 Multi-Agent Systems

A multi-agent architecture divides a complex application into specialized components or agents, each responsible for a specific sub-task. For example, one agent node may specialize in intent planning, another in flight offer retrieval, and another in destination recommendations.

Using specialized agent nodes simplifies system design, improves testability, and allows independent error handling across different conversational operations.

## 1.6 LangGraph

LangGraph is a state-graph orchestration framework designed for building multi-step agent workflows. Unlike linear chain frameworks, LangGraph models agent interaction as a state machine with nodes (execution steps) and conditional edges (routing logic).

In this system, LangGraph manages the state of each conversation turn. A turn begins at the Planner node, passes through Context Loading, routes to a specialized execution node (e.g., Flight Search or Trip Inspiration), builds a safe UI result, and persists the turn checkpoint in PostgreSQL.

## 1.7 REST APIs

REST (Representational State Transfer) is an architectural style for building web APIs using standard HTTP methods (GET, POST, PUT, DELETE). The backend exposes REST endpoints to communicate with the React frontend and connects to external REST services for flight data (Duffel) and forecasts (OpenWeather).

Integration with third-party APIs requires robust client design, including authorization headers, request serialization, rate limit retries, timeout management, and mapping provider errors to application exceptions.

## 1.8 Database

Relational database management systems (RDBMS) provide durable storage, ACID transaction guarantees, and structured schema enforcement. This application uses PostgreSQL as its primary database.

Object-Relational Mapping (ORM) tools, such as SQLAlchemy 2 in Python, map database tables to Python classes. Database migrations (managed via Alembic) version-control the schema evolution over time, allowing safe deployment updates across environments.

## 1.9 Authentication and Security

- **Authentication** verifies the identity of a user logging into the system.
- **Authorization** determines whether an authenticated user has permission to access specific resources or execute actions.

The authentication layer follows the OpenID Connect (OIDC) standard and supports compatible identity providers (such as Auth0 or Keycloak). In the current deployment configuration, Auth0 is set up as the primary active identity provider. Authorization policies ensure that users can view and manage only their own conversation threads, traveler profiles, quotes, and booking records.

System security controls include:
- **Session Cookie Security:** The backend exchanges validated OIDC tokens for an encrypted, HTTP-only session cookie (`session_token`), protecting tokens from client-side script access.
- **Cross-Site Request Forgery (CSRF) Protection:** State-changing requests require a valid `X-CSRF-Token` header.
- **Personally Identifiable Information (PII) Encryption:** Sensitive traveler data (passport numbers, dates of birth, phone numbers) are encrypted at rest in PostgreSQL using AES-256-GCM authenticated encryption (`cryptography.hazmat.primitives.ciphers.aead.AESGCM`) with per-field authenticated associated data (AAD), random 12-byte nonces, and key versioning support.

## 1.10 Technologies Used

The core technology stack consists of:
- **Python >= 3.11 & FastAPI:** Modern asynchronous Web API framework (compatible with Python 3.11, 3.12, 3.13, 3.14+).
- **Pydantic v2:** Fast data validation library using Python type hints.
- **LangGraph v0.2:** State graph framework for orchestrating dialogue agent workflows.
- **SQLAlchemy 2 & Alembic:** SQL ORM toolkit and schema migration management.
- **PostgreSQL:** Durable relational database system.
- **React 19 & TypeScript:** Frontend web application framework with static typing.
- **TanStack Query (React Query v5):** Asynchronous state management for API fetching, caching, and state synchronization.
- **Vite:** High-performance frontend build tool.

---

# CHAPTER 2. SYSTEM ANALYSIS AND DESIGN

## 2.1 System Overview

The Multi-Agent Flight Booking System is an integrated web platform combining a natural language chat interface with a structured flight search form. Users can search for flights, inspect destination weather, view curated local recommendations, manage saved traveler profiles, set price alerts (watches), and execute sandbox bookings.

## 2.2 Stakeholders

- **End Users:** Travelers using the web app to search for flights, inspect recommendations, set price watches, and complete sandbox booking orders.
- **Software Developers & Operators:** Engineers deploying backend services, configuring API keys (Duffel, OpenWeather, Auth0, LLM), monitoring log traces, and managing database schema migrations.
- **Academic Evaluators:** Reviewers assessing software architecture, engineering decisions, codebase organization, and verification evidence.

## 2.3 Functional Requirements

The functional capabilities implemented in the project are summarized in Table 2.1.

*Table 2.1: Functional Requirements*

| Req ID | Description | Implementation Status |
|---|---|---|
| **FR-01** | The system supports user authentication via an OIDC provider (Auth0/Keycloak). | Implemented |
| **FR-02** | The system creates secure HTTP-only backend session cookies upon successful login. | Implemented |
| **FR-03** | The system allows users to create, list, view, and delete conversation threads. | Implemented |
| **FR-04** | The system accepts natural language messages and processes them through an LLM planner. | Implemented |
| **FR-05** | The system resolves city/country names and misspelled text to valid IATA airport codes. | Implemented |
| **FR-06** | The system calculates exact date windows from relative date expressions using the server timezone. | Implemented |
| **FR-07** | The system searches live flight offers via the Duffel flight API. | Implemented |
| **FR-08** | The system filters and ranks flight offers by price, flight duration, stops, or baggage allowance. | Implemented |
| **FR-09** | The system supports destination inspiration based on airfare budget limits. | Implemented |
| **FR-10** | The system fetches optional destination weather forecasts (OpenWeather) without blocking flight results. | Implemented |
| **FR-11** | The system displays curated place recommendations for destination cities. | Implemented |
| **FR-12** | The system allows users to save and manage traveler profiles (passport, DOB, contact details). | Implemented |
| **FR-13** | The system verifies offer price quotes and traveler completeness before booking confirmation. | Implemented |
| **FR-14** | The system creates sandbox booking orders via Duffel with idempotency protection. | Implemented |
| **FR-15** | The system allows users to create price watches and run background price checks. | Implemented |
| **FR-16** | The system encrypts sensitive traveler PII in the database via AES-256-GCM authenticated encryption. | Implemented |

## 2.4 Non-functional Requirements

- **Reliability:** Third-party API failures (e.g., weather or place recommendation outages) must degrade gracefully without crashing core flight search workflows.
- **Security:** State-changing API endpoints require authenticated sessions and valid CSRF token headers. Sensitive fields are encrypted at rest.
- **Performance:** Location resolution uses in-memory airport caching. Provider requests use bounded HTTP timeouts (e.g., 10s for Duffel searches).
- **Maintainability:** Clear separation between API route handlers, domain services, provider adapters, and database repositories.

## 2.5 Actors

- **Traveler / User:** Authenticated user who interacts with the natural language chat or structured search forms.
- **LLM Planner Service:** Remote LLM API that interprets user text into structured JSON commands.
- **Flight Provider (Duffel):** External service providing flight availability, offer prices, and sandbox order execution.
- **Enrichment Service (OpenWeather):** External provider supplying destination weather forecasts.
- **Identity Provider (Auth0/Keycloak):** External OIDC service handling primary user authentication.

## 2.6 Use Cases

```text
                       +-----------------------------------+
                       |    Flight Booking System          |
                       |                                   |
                       |  (UC-01) Conversational Search    |
                       |  (UC-02) Destination Inspiration  |
   +--------------+    |  (UC-03) Direct Flight Search     |    +-------------------+
   |   Traveler   |===>|  (UC-04) Confirm Sandbox Booking  |===>| External Providers|
   |    (User)    |    |  (UC-05) Manage Traveler Profile  |    | (Duffel / Auth0)  |
   +--------------+    |  (UC-06) Create Price Watch       |    +-------------------+
                       |                                   |
                       +-----------------------------------+
```
*Figure 2.2 – Use Case Diagram*

### UC-01: Conversational Flight Search
1. User enters a chat message (e.g., *"Find flights from Hanoi to Da Nang next Tuesday"*).
2. Frontend posts to `/api/v1/threads/{thread_id}/messages`.
3. LLM planner extracts origin (*Hanoi*), destination (*Da Nang*), and relative date (*next Tuesday*).
4. Location resolver maps *Hanoi* → `HAN` and *Da Nang* → `DAD`.
5. Date resolver calculates *next Tuesday* to an exact ISO date string (e.g., `2026-08-25`).
6. Flight service queries Duffel API for live offers.
7. Offers are ranked, saved to PostgreSQL, and rendered as UI offer cards.

### UC-02: Destination Inspiration within Budget
1. User asks: *"Where can I fly from Ho Chi Minh City with a budget of 3,000,000 VND?"*
2. Planner identifies command as `trip_inspiration` with origin `SGN` and budget `3,000,000 VND`.
3. System selects candidate destinations (`DAD`, `PQC`, `BKK`, `SIN`), queries Duffel, and converts currencies.
4. Matching destinations with fares under 3,000,000 VND are returned as inspiration cards.

### UC-03: Direct Search via Flights Form
1. User navigates to the **Flights** tab.
2. User fills out origin (`HAN`), destination (`SIN`), date (`2026-09-01`), passengers, and cabin.
3. Frontend submits POST request to `/api/v1/flights/search`.
4. Backend searches Duffel and renders matching offer cards.

### UC-04: Sandbox Flight Booking Confirmation
1. User selects a flight offer card and clicks **Book Flight**.
2. Frontend opens booking modal and prompts traveler selection.
3. Backend checks quote freshness and verifies traveler completeness.
4. User clicks **Confirm Sandbox Booking**.
5. Backend attaches idempotency key and submits order request to Duffel sandbox endpoint.
6. Duffel returns sandbox order reference (`ord_0000Axxx`).
7. Backend saves `BookingRecord` with status `order_created`.

![Login Page Screenshot](frontend/public/screenshots/login_page.png)  
*Figure 2.5 – User Login Interface Screen*

![Assistant Chat Interface](frontend/public/screenshots/assistant_chat_page.png)  
*Figure 2.6 – Assistant Conversational Search & Structured Offer Cards Screen*

![Flight Search Form Page](frontend/public/screenshots/flight_search_page.png)  
*Figure 2.7 – Direct Flight Search Form Interface Screen*

## 2.7 System Architecture

```text
+-------------------------------------------------------------------+
|                        React 19 Frontend UI                       |
|   (Assistant Page, Flights Page, Bookings Page, Travelers Page)   |
+-------------------------------------------------------------------+
                                 |
                                 | HTTP REST APIs (Cookies, CSRF, JSON)
                                 v
+-------------------------------------------------------------------+
|                         FastAPI Backend                           |
|  +-------------------------------------------------------------+  |
|  | API Layer: Auth Router, Product Router, Operations Router   |  |
|  +-------------------------------------------------------------+  |
|  | Orchestration Layer: LangGraph State Graph & LLM Planner   |  |
|  +-------------------------------------------------------------+  |
|  | Domain Services: Location, Date, Flight, Booking, Watch     |  |
|  +-------------------------------------------------------------+  |
|  | Provider Adapters: Duffel, OpenWeather, LLM, Curated Places |  |
|  +-------------------------------------------------------------+  |
+-------------------------------------------------------------------+
                                 |
                                 v
+-------------------------------------------------------------------+
|                     PostgreSQL Database                           |
|       (24 Model Classes: Users, Sessions, Threads, Searches,      |
|             Offers, Quotes, Bookings, Watches, Audits)            |
+-------------------------------------------------------------------+
```
*Figure 2.1 – System Architecture Diagram*

## 2.8 Database Design

The database schema is defined in `src/agent_system/db/models.py` and managed via Alembic migrations.

```text
[UserRecord] 1 --- * [UserSessionRecord]
     | 1
     +--------- * [TravelerProfileRecord]
     | 1
     +--------- * [ChatThreadRecord] 1 --- * [ChatMessageRecord]
     | 1
     +--------- * [FlightSearchRecord] 1 --- * [FlightOfferRecord]
     | 1
     +--------- * [BookingRecord] 1 --- * [BookingEventRecord]
     | 1
     +--------- * [FlightWatchRecord] 1 --- * [WatchMatchRecord]
```
*Figure 2.3 – ERD (Database Schema Overview)*

The 24 table model classes in `src/agent_system/db/models.py` include: `UserRecord`, `UserSessionRecord`, `TravelerProfileRecord`, `UserTravelPreferenceRecord`, `ChatThreadRecord`, `ChatMessageRecord`, `AgentCheckpointRecord`, `FlightSearchRecord`, `FlightOfferRecord`, `FlightDiscoveryRecord`, `FlightSearchAttemptRecord`, `BookingQuoteRecord`, `BookingIntentRecord`, `BookingRecord`, `BookingEventRecord`, `BookingOperationRecord`, `PurchaseMandateRecord`, `FlightWatchRecord`, `WatchRunRecord`, `WatchMatchRecord`, `WatchHoldRecord`, `WatchNotificationRecord`, `AuditEventRecord`, and `OutboxEventRecord`.

## 2.9 Multi-Agent Design

The project uses a **LangGraph-based agentic workflow architecture** where specialized graph nodes process conversation state through explicit routing:
- `PlannerNode`: Invokes LLM adapter to parse user intent and structured schemas.
- `ContextLoaderNode`: Retrieves thread message history and previous search offer references.
- `FlightSearchNode`: Calls Duffel API adapter for offer retrieval.
- `TripInspirationNode`: Evaluates destination candidates against user budget limits.
- `BookingNode`: Validates traveler completeness and verifies quote freshness.
- `SafeResultBuilderNode`: Strips raw provider secrets and constructs UI response payloads.

## 2.10 Conversation Management

```text
[Start Turn] 
     │
     ▼
[Planner Node] ──► (LLM interprets intent & updates structured schema)
     │
     ▼
[Context Loader] ──► (Loads recent thread history & previous search results)
     │
     ▼
[Route Execution] 
     ├──► [Trip Discovery Node]
     ├──► [Flight Search Node]
     ├──► [Destination Inspiration Node]
     ├──► [Booking Node]
     └──► [Clarification / Advice Node]
     │
     ▼
[Safe Result Builder] ──► (Strips raw tokens/secrets & prepares UI payload)
     │
     ▼
[Database Persistence] ──► (Saves ChatMessageRecord & Checkpoint)
```
*Figure 2.4 – Conversation Processing Sequence*

## 2.11 Flight Search

- Converts criteria into Duffel API payloads via `flight_search.py`.
- Normalizes raw JSON offers into standard internal models.
- Sorts offers by price, duration, or number of stops via `flight_ranking.py`.

## 2.12 Recommendation

- Loads curated destination points of interest from `curated_destinations.v1.json`.
- Attaches explicit `source` metadata (`curated_v1`) to distinguish database catalog entries from LLM descriptions.

## 2.13 Weather

- Queries 3-day forecasts for destination airport coordinates via OpenWeather.
- Caches forecast results in memory for 3 hours.
- Handles provider failures by marking weather status as `unavailable` without blocking flight offer results.

## 2.14 Booking Workflow

```text
[User Clicks "Book"] ──► POST /api/v1/bookings/intents
                              │
                              ▼
                   [Verify Quote & Reprice]
                              │
                              ▼
                   [Validate Traveler PII]
                              │
                              ▼
                [User Confirms in UI Dialog]
                              │
                              ▼
               POST /api/v1/bookings/confirm
                              │
                              ▼
              [Submit Order to Duffel Sandbox]
                              │
                              ▼
            [Save BookingRecord (status: order_created)]
```
*Figure 2.8 – Booking Sequence Diagram*

![Bookings Overview Page](frontend/public/screenshots/bookings_page.png)  
*Figure 2.9 – Booking Management Overview Screen*

![Travelers Management Page](frontend/public/screenshots/travelers_page.png)  
*Figure 2.10 – Traveler Profile Management Screen*

![Price Alerts Page](frontend/public/screenshots/watches_page.png)  
*Figure 2.11 – Price Alerts & Watch Monitoring Screen*

## 2.15 Authentication

- User authenticates via Auth0 OIDC authorization code flow.
- Frontend posts authorization code to backend `/api/v1/auth/session`.
- Backend verifies token with Auth0, creates user session in PostgreSQL, and sets an encrypted HTTP-only session cookie.

## 2.16 Security

- **Session Cookies:** Encrypted HTTP-only cookies protect tokens against client script access.
- **CSRF Token:** Required on all API state mutations via `X-CSRF-Token` headers.
- **PII Encryption:** Sensitive traveler fields (passport numbers) are encrypted at rest using AES-256-GCM authenticated encryption (`cryptography.hazmat.primitives.ciphers.aead.AESGCM`) with per-field authenticated associated data (AAD).

## 2.17 Error Handling

Maps domain exceptions to HTTP status codes: parameter errors → `400 Bad Request`, expired quotes → `409 Conflict`, provider timeouts → `504 Gateway Timeout`, server errors → `500 Internal Server Error` (with trace ID logging).

## 2.18 Idempotency

Idempotency keys (`user_id + offer_id + attempt`) prevent duplicate provider orders during retries. Turn locks ensure thread safety during concurrent message processing.

---

# CHAPTER 3. MODEL TRAINING AND SYSTEM INTEGRATION

## 3.1 AI Model Approach

The application uses an external LLM purely for natural language interpretation. The model is treated as a probabilistic language parser rather than a travel database. All factual data (airports, flight offers, fares, dates, bookings) are managed by backend services.

## 3.2 Model Training Status

> **Official Status Statement on Model Training:**  
> The current project **does not train or fine-tune a custom language model**. The codebase contains no training datasets, epoch loss scripts, or custom model weights. Instead, it integrates a general-purpose language model through an OpenAI-compatible API adapter and improves reliability through prompt design, structured output validation, deterministic domain rules, and integration testing.

## 3.3 LLM Configuration

The LLM provider adapter (`src/agent_system/llm_providers.py`) connects to OpenAI-compatible endpoints using `httpx`. The adapter configuration uses temperature `0.0` to enforce deterministic formatting.

## 3.4 Prompt Design

System prompts define the role of the LLM planner:
- Parse user travel goals into structured commands (`search_flights`, `trip_inspiration`, `start_booking`).
- Extract origin, destination, relative dates, budget, and ranking preferences.
- Strictly refrain from generating imaginary IATA codes, flight fares, or booking codes.

## 3.5 Structured Output

The LLM adapter enforces JSON response formats matching Pydantic schemas:
```json
{
  "command": "search_flights",
  "language": "vi",
  "plan": "User wants flights from Hanoi to Singapore next Tuesday under 5 million VND.",
  "dialogue_act": "inform_search_criteria",
  "interpreted_destination": "Singapore",
  "destination_scope": "city",
  "semantic_updates": {
    "origin": { "raw_text": "Hanoi", "normalized": "hanoi", "confidence": 0.98 },
    "destination": { "raw_text": "Singapore", "normalized": "singapore", "confidence": 0.99 },
    "temporal": { "expression_type": "relative", "raw_expression": "next Tuesday", "relative_offset_days": 7 },
    "budget": { "amount": 5000000.0, "currency": "VND" },
    "optimization": { "preference": "cheapest" }
  }
}
```

## 3.6 Multi-Agent Orchestration

LangGraph manages graph transitions. Nodes process incoming messages, load chat history, route tasks to specialized domain services, and persist state checkpoints.

```text
+-----------------------------------------------------------------------------------+
|                        LangGraph Agentic Workflow Orchestration                   |
|                                                                                   |
| [User Message]                                                                    |
|       │                                                                           |
|       ▼                                                                           |
| [Planner Node] ──► [Context Loader] ──► [Route Dispatcher]                        |
|                                                │                                  |
|                 ┌──────────────────────────────┼──────────────────────────────┐   |
|                 ▼                              ▼                              ▼   |
|        [Flight Search Node]           [Inspiration Node]              [Booking Node] |
|                 │                              │                              │   |
|                 └──────────────────────────────┼──────────────────────────────┘   |
|                                                ▼                                  |
|                                    [Safe Result Builder Node]                     |
+-----------------------------------------------------------------------------------+
```
*Figure 3.1 – LangGraph Agentic Workflow Flow*

## 3.7 Semantic Processing

Extracted parameters undergo normalization:
- Location text is normalized by removing diacritics (`đ` → `d`).
- Relative dates are calculated relative to server timezone (`Asia/Ho_Chi_Minh`).
- Foreign budget amounts are converted using validated exchange rates.

## 3.8 Deterministic Validation

Before executing API actions, backend logic validates:
- Airport codes exist in the validated catalog.
- Departure dates are not in the past.
- Search date windows do not exceed 7 days.
- Passenger counts are within supported limits.

## 3.9 Flight API Integration

The Duffel provider adapter communicates with Duffel API v2 (`https://api.duffel.com/air/`).

- `client.py`: Sends HTTP requests with `Authorization: Bearer <token>` and `Duffel-Version: v2` headers. Handles rate limit retries (HTTP 429) and timeouts.
- `flights.py`: Maps internal criteria to Duffel `/air/offer_requests` and parses returned offer slices.
- `orders`: Submits sandbox booking orders to `/air/orders` using balance settlement.

## 3.10 Weather Integration

Queries 3-day destination forecasts via OpenWeather API. Caches responses in memory for 3 hours. If OpenWeather fails, weather status is set to `unavailable` without blocking flight search outputs.

## 3.11 Database Integration

SQLAlchemy 2 async sessions manage database persistence. Alembic migration scripts version table schema changes.

## 3.12 Authentication Integration

Auth0 OIDC tokens are verified backend-side. Successful login issues an encrypted HTTP-only session cookie alongside a CSRF token.

## 3.13 Booking Integration

`booking_workflow.py` enforces quote validity, traveler completeness, idempotency keys, and sandbox order execution via Duffel.

## 3.14 Error Handling

Third-party API errors or LLM schema validation failures trigger controlled fallbacks, such as asking a clarification question or returning a user-friendly error message with a trace ID.

## 3.15 End-to-End Data Flow

```text
+-----------------------------------------------------------------------------------+
|                           End-to-End System Data Flow                             |
|                                                                                   |
|  User Natural Text / Form Input                                                   |
|           │                                                                       |
|           ▼                                                                       |
|  React 19 Frontend UI (Credentials + CSRF Header)                                 |
|           │                                                                       |
|           ▼                                                                       |
|  FastAPI Router (Session Authentication & Input Validation)                       |
|           │                                                                       |
|           ▼                                                                       |
|  LangGraph Planner & Context Loader (Thread History from PostgreSQL)              |
|           │                                                                       |
|           ▼                                                                       |
|  Domain Resolvers (Diacritic Location Normalization & Server Timezone Dates)      |
|           │                                                                       |
|           ▼                                                                       |
|  Duffel Flight API Adapter (Offers & Sandbox Orders)                              |
|           │                                                                       |
|           ▼                                                                       |
|  Safe Result Builder (Strips Provider Secrets & Persists ChatMessageRecord)       |
|           │                                                                       |
|           ▼                                                                       |
|  Frontend UI Structured Card Render                                               |
+-----------------------------------------------------------------------------------+
```
*Figure 3.2 – External Provider Integration & End-to-End Data Flow*

---

# CHAPTER 4. RESULTS, TESTING AND SYSTEM EVALUATION

## 4.1 Testing Strategy

System verification combines backend unit/integration tests, frontend component tests, and build quality checks:
1. **Backend Tests (Pytest):** Tests domain logic, date math, location resolution, LLM schema parsing, Duffel API mapping, booking gates, and security middleware.
2. **Frontend Tests (Vitest):** Tests React components, hooks, API services, booking dialogs, and route protection.
3. **Quality Checks:** Static type checking (`tsc`), ESLint validation, and Ruff Python linting.

## 4.2 Test Environment

Tests were executed against the repository source code using the local Python environment and Node frontend setup. Backend tests use SQLite test databases and mock provider response fixtures for speed and reproducibility.

## 4.3 Backend Testing

Backend testing uses pytest. Default test configuration excludes tests marked `provider` (which require live sandbox API credentials and network access).

## 4.4 Frontend Testing

Frontend component tests use Vitest and React Testing Library (`frontend/src/**/*.test.tsx`).

## 4.5 Unit Testing

Unit tests cover isolated functions: date parsing, diacritic normalization, edit-distance matching, exchange rate conversions, and PII encryption/decryption.

## 4.6 Integration Testing

Integration tests verify API routes, session cookie validation, CSRF token checks, and multi-turn conversation graph state transitions.

## 4.7 Booking Testing

Booking tests in `test_phase8_duffel_balance_booking.py` verify that submitting valid traveler details creates a Duffel sandbox order and records a local `BookingRecord` with status `order_created`.

## 4.8 Security Testing

Security tests verify that unauthenticated requests return HTTP 401 Unauthorized, mutations missing CSRF tokens return HTTP 403 Forbidden, and traveler passport numbers are encrypted in database tables.

## 4.9 LLM-related Testing

LLM tests in `test_deepseek_semantic_updates.py` verify that the planner adapter rejects malformed JSON and correctly extracts semantic parameters from test prompt payloads.

## 4.10 Test Results

### Backend Test Suite Results (Pytest)
The complete backend test suite completed with **605 passed test executions**, 16 skipped tests, and 2 deselected tests under default pytest configuration. Selected test groups were reviewed separately to demonstrate coverage across conversational AI, search, booking, and security functionality.

*Table 4.1: Representative Backend Test Suite Grouping*

| Test Area Category | Representative Test Files Included | Outcome | Tested Capabilities |
|---|---|---|---|
| **Conversational & Semantic** | `test_deepseek_semantic_updates.py`, `test_phase1_trip_discovery.py`, `test_turn_language.py`, `test_chat_memory.py` | Passed (110 cases) | Planner schema parsing, relative dates, language selection, typo handling, dialogue memory context. |
| **Search, Ranking & Weather** | `test_exchange_rates.py`, `test_phase5_destination_recommendations.py`, `test_openweather_and_services.py`, `test_trip_inspiration.py` | Passed (127 cases) | Currency conversion, candidate search, offer ranking, place catalog, weather fallback. |
| **Booking & Security** | `test_phase6_booking_workflow.py`, `test_phase8_duffel_balance_booking.py`, `test_security_phase2.py`, `test_auth_phase2.py` | Passed (35 cases) | Quote verification, traveler completeness, sandbox order creation, OIDC validation, PII encryption. |

### Frontend Test & Build Results
```text
Vitest Run: 19 test files passed, 100 tests passed (18.66s)
ESLint: 0 errors, 0 warnings
Vite Build: 2,192 modules transformed, dist/ index bundle built successfully
```

## 4.11 Sample User Verification Scenarios

*Table 4.2: Sample User Verification Scenarios*

| Scenario | Input Query / Action | Expected Result | Actual Result | Status |
|---|---|---|---|---|
| **Conversational Flight Search** | *"Flights Hanoi to Singapore next Tuesday"* | Resolves `HAN` -> `SIN`, calculates date, queries Duffel | Offers returned | Pass |
| **Relative Date Calculation** | *"next Friday"* | Evaluates date from server timezone (`Asia/Ho_Chi_Minh`) | Correct ISO date string | Pass |
| **Diacritic Typo Handling** | *"hanoii to bangcok"* | Normalizes diacritics, maps edit-distance typos | Resolves `HAN` and `BKK` | Pass |
| **Airfare Budget Filter** | *"Under 5 million VND"* | Converts currency & filters exceeding fares | Valid low-fare cards | Pass |
| **Sandbox Flight Booking** | Select offer + complete traveler profile | Validates quote & submits Duffel sandbox order | Status `order_created` | Pass |
| **Incomplete Traveler Gate** | Attempt booking without Date of Birth | Rejects booking request prior to provider API call | `traveler_incomplete` error | Pass |

## 4.12 Evaluation Against Objectives

*Table 4.3: Objective Evaluation Summary*

| Objective | Verification Evidence | Evaluation Result |
|---|---|---|
| **Conversational Travel Interpretation** | 110 semantic & planner test executions | Achieved under tested prompt conditions |
| **Typo-Tolerant Location Resolution** | Location resolution unit tests | Achieved for catalog airports and cities |
| **Date & Budget Validation** | Date parsing & exchange rate tests | Achieved for server timezone date math |
| **Flight Search & Ranking** | Duffel adapter & offer ranking tests | Achieved for price/duration/stops sorting |
| **Optional Weather & Places** | Weather fallback & places catalog tests | Achieved with non-blocking error handling |
| **Safe Booking Workflow** | 35 booking & security test executions | Achieved in Duffel sandbox environment |
| **Security & Privacy Protection** | OIDC, session & AES-GCM PII tests | Achieved for session cookies & PII encryption |

## 4.13 Limitations

1. **No Fine-Tuned Model Benchmarks:** The project did not train a custom LLM or publish formal intent-accuracy metrics against public datasets.
2. **Offline Unit Test Scope:** Pytest runs use mock provider responses and fixture payloads. Passing tests verify software logic, not 100% live API availability.
3. **Sandbox Order Scope:** Duffel booking execution was tested exclusively against Duffel's test sandbox. The system does not process live payments or issue production tickets.
4. **Airfare-Only Budgeting:** Budget filtering applies strictly to airfares; lodging, food, and local transport costs are not calculated.
5. **No Human Usability Study:** Usability conclusions are based on component testing rather than formal participant user studies.

## 4.14 Threats to Validity

- **LLM Output Variability:** Remote LLMs may occasionally return unexpected output formatting not observed during offline testing.
- **Provider API Volatility:** Real-world flight prices and seat availability change rapidly; mock provider tests cannot predict live fare availability.
- **Environment Configuration:** Automated tests execute against SQLite and local test databases; production deployments on PostgreSQL require proper environment variable configuration.

---

# CONCLUSION AND FUTURE DEVELOPMENT

## Conclusion

This project successfully designed, implemented, and evaluated an AI-assisted flight search and booking system combining conversational interface capabilities with deterministic backend domain controls.

The primary contribution of this work is its **hybrid control architecture**:
- A remote LLM (via OpenAI-compatible API) interprets natural language user requests, tracks multi-turn conversation context, and extracts structured travel parameters.
- Deterministic Python/FastAPI services manage location resolution, relative date calculations, currency conversion, Duffel API searches, PostgreSQL persistence, and booking workflow state transitions.

This strict architectural separation reduces the risk of the LLM generating unsupported flight numbers, airport codes, ticket prices, or booking confirmations. The system includes 24 PostgreSQL database tables, robust OIDC authentication, session cookie security, CSRF protection, AES-256-GCM PII encryption, and responsive frontend UI components. Software verification confirmed that the backend test suite (605 passed test executions) and frontend test suite (100 passed tests across 19 files) meet system quality and safety standards.

## Future Development

Future software enhancements include:
1. **Multilingual Dataset Evaluation:** Collect and annotate a benchmark dataset of Vietnamese and English travel queries to measure LLM intent parsing accuracy formally.
2. **Live Exchange Rate Provider:** Integrate a real-time financial FX rate API to replace static demo exchange rates.
3. **Full Trip Budget Estimation:** Expand trip inspiration features to include lodging and local transport estimates alongside airfares.
4. **Production Payment Gateway:** Integrate a payment gateway (e.g., Stripe) to support real production ticket issuance.
5. **Enhanced Disambiguation UI:** Improve frontend modal dialogs when resolving ambiguous country-level destinations with multiple airports.
6. **Mobile Application Development:** Build a dedicated mobile client (React Native) consuming the existing REST API backend.

---

# REFERENCES

1. Auth0. *Authorization Code Flow with PKCE Documentation*. Auth0 Docs. `https://auth0.com/docs/get-started/authentication-and-authorization-flow/authorization-code-flow`
2. Duffel Financial Ltd. *Duffel Flight API v2 Reference Documentation*. Duffel Developer Portal. `https://duffel.com/docs/api`
3. FastAPI Framework. *FastAPI Official Documentation*. Tiangolo. `https://fastapi.tiangolo.com/`
4. LangChain / LangGraph. *LangGraph Orchestration & State Graph Documentation*. LangChain AI. `https://langchain-ai.github.io/langgraph/`
5. OpenWeather Ltd. *OpenWeather Current Weather and Forecast API*. OpenWeather. `https://openweathermap.org/api`
6. PostgreSQL Global Development Group. *PostgreSQL 16 Documentation*. PostgreSQL. `https://www.postgresql.org/docs/16/index.html`
7. Pydantic Team. *Pydantic Data Validation Documentation*. Pydantic. `https://docs.pydantic.dev/`
8. React Core Team. *React 19 Documentation*. Meta Open Source. `https://react.dev/`
9. OpenID Foundation. *OpenID Connect Core 1.0 incorporating errata set 1*. OpenID Foundation. `https://openid.net/specs/openid-connect-core-1_0.html`
10. Internet Engineering Task Force (IETF). *RFC 6749: The OAuth 2.0 Authorization Framework*. IETF. `https://datatracker.ietf.org/doc/html/rfc6749`
11. OWASP Foundation. *Cross-Site Request Forgery (CSRF) Prevention Cheat Sheet*. OWASP. `https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html`
12. Python Cryptography Authority. *Authenticated Encryption with Associated Data (AEAD) Documentation*. PyCA Cryptography. `https://cryptography.io/en/latest/hazmat/primitives/aead/`
13. TanStack. *TanStack Query v5 Documentation*. TanStack. `https://tanstack.com/query/v5`
14. Vite Core Team. *Vite Next Generation Frontend Tooling*. Vite. `https://vitejs.dev/`
15. SQLAlchemy Authors. *SQLAlchemy 2.0 Documentation*. SQLAlchemy. `https://docs.sqlalchemy.org/en/20/`

---

# APPENDICES

This section provides a structured technical reference for quick information retrieval.

---

## Appendix A: Source Code & Implementation Evidence Mapping

*Table A.1: Primary Implementation Evidence Index*

| Reference ID | Source Code Location | Architectural Role & Implementation Details |
|---|---|---|
| **E-01** | `src/agent_system/main.py` | FastAPI application entry point, CORS middleware, trace ID logging, global exception mapping. |
| **E-02** | `src/agent_system/orchestration_graph.py` | LangGraph state graph definition, planner node, turn routing, context loading, safe result building. |
| **E-03** | `src/agent_system/llm_providers.py` | OpenAI-compatible LLM HTTP client adapter, DeepSeek prompt formatting, structured JSON output validation. |
| **E-04** | `src/agent_system/services/location_resolution.py` | Location resolution service, diacritic normalization (`đ` → `d`), edit-distance typo matching, airport catalog lookup. |
| **E-05** | `src/agent_system/services/date_resolution.py` | Timezone-aware date calculations (`Asia/Ho_Chi_Minh`), relative expression parsing, search window validation. |
| **E-06** | `src/agent_system/providers/duffel/flights.py` | Duffel API offer request mapping, price breakdown extraction, baggage policy parsing, sandbox order payloads. |
| **E-07** | `src/agent_system/providers/duffel/client.py` | Duffel HTTP client, bearer token headers, API versioning (`v2`), rate limit retries, typed provider exceptions. |
| **E-08** | `src/agent_system/services/booking_workflow.py` | Booking gate safety rules, quote freshness checks, traveler profile completeness validation, idempotency controls. |
| **E-09** | `src/agent_system/db/models.py` | 24 SQLAlchemy table model definitions for identity, chat, searches, offers, quotes, bookings, and alerts. |
| **E-10** | `alembic/versions/` | 16 Alembic database migration scripts enforcing schema constraints and indexes. |
| **E-11** | `src/agent_system/auth/oidc.py` & `sessions.py` | Auth0 OIDC JWT verification, database-backed session token hashing, HTTP-only cookie management, CSRF token validation. |
| **E-12** | `src/agent_system/security/encryption.py` | AES-256-GCM authenticated encryption (AEAD) with key versioning and rotation for sensitive traveler PII database fields. |
| **E-13** | `frontend/src/pages/AssistantPage.tsx` | React 19 natural language chat page with structured offer cards and enrichment widgets. |
| **E-14** | `frontend/src/pages/SearchPage.tsx` | React 19 direct flight search form page with origin, destination, date, and passenger inputs. |
| **E-15** | `frontend/src/pages/BookingsPage.tsx` | React 19 booking management overview page showing sandbox orders, confirmation codes, and quotes. |
| **E-16** | `frontend/src/pages/TravelersPage.tsx` | React 19 traveler profile management page with readiness completeness indicators. |
| **E-17** | `frontend/src/pages/WatchesPage.tsx` | React 19 price alert watch management page with route monitoring and notification status. |
| **E-18** | `frontend/src/pages/LoginPage.tsx` | React 19 user login and OIDC authentication entry page. |
| **E-19** | `Dockerfile` & `frontend/Dockerfile` | Production Docker container build manifests for backend Uvicorn server and frontend Nginx web server. |
| **E-20** | `pyproject.toml` | Python project configuration, system dependencies (`FastAPI`, `LangGraph`, `SQLAlchemy`), pytest options. |

---

## Appendix B: Database Schema & Entity Quick Reference (24 Model Classes)

*Table A.2: Database Model Classes Summary (`src/agent_system/db/models.py`)*

| Area Category | Table Model Class Name | Table Name | Purpose & Stored Attributes |
|---|---|---|---|
| **Identity & Sessions** | `UserRecord` | `users` | User identity (OIDC issuer/subject, email, locale, timezone, account status). |
| | `UserSessionRecord` | `user_sessions` | Active user sessions (session token hash, CSRF token hash, expiry, device label). |
| | `TravelerProfileRecord` | `traveler_profiles` | Saved traveler profile (encrypted passport/national ID, full name, DOB, completeness status). |
| | `UserTravelPreferenceRecord` | `user_travel_preferences` | Saved travel preferences (home airport, seating preference, preferred airlines). |
| **Conversation State** | `ChatThreadRecord` | `chat_threads` | Conversation thread container (user ID, title, status, timestamps). |
| | `ChatMessageRecord` | `chat_messages` | Messages (thread ID, role `user`/`assistant`, content, safe result JSON). |
| | `AgentCheckpointRecord` | `agent_checkpoints` | LangGraph orchestration graph checkpoint state for multi-turn execution. |
| **Search & Offers** | `FlightSearchRecord` | `flight_searches` | Submitted search criteria (origin, destination, date, passengers, cabin). |
| | `FlightOfferRecord` | `flight_offers` | Flight offer data (price, currency, airline, itinerary segments, offer expiry). |
| | `FlightDiscoveryRecord` | `flight_discoveries` | Stored candidate destination search discovery records. |
| | `FlightSearchAttemptRecord` | `flight_search_attempts` | Audit log of provider flight search attempts and execution latency. |
| **Booking & Orders** | `BookingQuoteRecord` | `booking_quotes` | Price quote reprice verification record and fare authority. |
| | `BookingIntentRecord` | `booking_intents` | Draft booking intent before explicit user confirmation. |
| | `BookingRecord` | `bookings` | Stored booking record (status `order_created`, Duffel order ID, confirmation code). |
| | `BookingEventRecord` | `booking_events` | Immutable audit log of booking state transitions and events. |
| | `BookingOperationRecord` | `booking_operations` | Idempotent log of provider booking API execution attempts. |
| | `PurchaseMandateRecord` | `purchase_mandates` | Stored user authorization mandate for booking operations. |
| **Price Alert Watches** | `FlightWatchRecord` | `flight_watches` | Active price watch definitions (route, target max price, status). |
| | `WatchRunRecord` | `watch_runs` | Execution log of periodic background watch checks. |
| | `WatchMatchRecord` | `watch_matches` | Matching flight offer found during a background watch run. |
| | `WatchHoldRecord` | `watch_holds` | Temporary hold record on a matched watch offer. |
| | `WatchNotificationRecord` | `watch_notifications` | Sent alert notification log for triggered watches. |
| **Operations & Audit** | `AuditEventRecord` | `audit_events` | System audit log for security events, authentication, and error tracking. |
| | `OutboxEventRecord` | `outbox_events` | Transactional outbox pattern table for asynchronous background events. |

---

## Appendix C: System REST API Endpoints Reference

*Table A.3: Core Backend REST API Routes (`src/agent_system/api/`)*

| Endpoint Path | HTTP Method | Auth Required | Purpose & Description |
|---|---|---|---|
| `/api/v1/auth/session` | `POST` | Public | Exchange OIDC authorization code for backend session cookie & CSRF token. |
| `/api/v1/auth/me` | `GET` | Session Cookie | Get current authenticated user profile & session metadata. |
| `/api/v1/auth/logout` | `POST` | Session Cookie | Revoke backend session cookie and log user out. |
| `/api/v1/threads` | `GET` / `POST` | Session Cookie | List active conversation threads or create a new conversation thread. |
| `/api/v1/threads/{thread_id}/messages` | `POST` | Session Cookie | Send a natural language message to the assistant; returns structured response. |
| `/api/v1/threads/{thread_id}/history` | `GET` | Session Cookie | Retrieve chronological chat message history for a specific thread. |
| `/api/v1/flights/search` | `POST` | Session Cookie | Execute direct flight search from the Flights form tab. |
| `/api/v1/travelers` | `GET` / `POST` | Session Cookie | List or create saved traveler profiles (PII auto-encrypted). |
| `/api/v1/travelers/{traveler_id}` | `PUT` / `DELETE` | Session Cookie | Update or delete an existing traveler profile. |
| `/api/v1/bookings/intents` | `POST` | Session Cookie | Create a draft booking intent and re-verify quote price freshness. |
| `/api/v1/bookings/confirm` | `POST` | Session Cookie | Submit explicit user confirmation and create Duffel sandbox order. |
| `/api/v1/bookings` | `GET` | Session Cookie | List all user booking records and inspect order status details. |
| `/api/v1/watches` | `GET` / `POST` | Session Cookie | List active price alert watches or create a new flight watch. |
| `/api/v1/operations/health` | `GET` | Public | System health check and database connection readiness check. |

---

## Appendix D: System Configuration & Environment Variables

*Table A.4: Essential Environment Settings (`src/agent_system/providers/settings.py`)*

| Environment Variable | Default / Sample Value | Purpose & Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://user:pass@localhost:5432/flight_db` | PostgreSQL database connection string. |
| `IDENTITY_ENABLED` | `true` | Enables OIDC authentication and session enforcement. |
| `OIDC_ISSUER` | `https://dev-auth0.us.auth0.com/` | OIDC provider token issuer URL. |
| `OIDC_AUDIENCE` | `https://api.flightbooking.com` | Expected OIDC API audience identifier. |
| `CORS_ORIGINS` | `http://localhost:5173` | Allowed frontend origin domains for CORS headers. |
| `FLIGHT_PROVIDER` | `duffel` | Selected flight offer search provider adapter (`duffel` or `mock`). |
| `DUFFEL_API_TOKEN` | `duffel_test_...` | API authentication token for Duffel flight API v2. |
| `BOOKING_ORDER_ENABLED` | `true` | Enables Duffel sandbox booking order creation workflows. |
| `WEATHER_PROVIDER` | `openweather` | Selected weather forecast provider (`openweather` or `mock`). |
| `OPENWEATHER_API_KEY` | `openweather_key_...` | API key for OpenWeather forecast API calls. |
| `LLM_PROVIDER` | `openai` | Selected LLM provider adapter (`openai` or `rule_based`). |
| `LLM_MODEL` | `deepseek-chat` | Remote LLM model name for chat completions. |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | Custom OpenAI-compatible API base endpoint URL. |
| `TRAVEL_TIMEZONE` | `Asia/Ho_Chi_Minh` | Server reference timezone for relative date parsing. |

---

## Appendix E: System Verification & Execution Commands

*Table A.5: Command Line Quick Reference*

| Command Description | Terminal Command Line | Expected Outcome / Verification Result |
|---|---|---|
| **Run Backend Unit Tests** | `uv run pytest -q` | `605 passed, 16 skipped, 2 deselected` (offline test suite). |
| **Run Conversational Tests** | `uv run pytest tests/test_deepseek_semantic_updates.py -q` | 110 semantic & planner tests pass. |
| **Run Booking Tests** | `uv run pytest tests/test_phase8_duffel_balance_booking.py -q` | Verified sandbox order creation & quote gates. |
| **Run Frontend Unit Tests** | `npm test -- --run` (in `frontend/`) | `19 test files passed, 100 tests passed`. |
| **Run Frontend Linter** | `npm run lint` (in `frontend/`) | Exit code `0` with 0 ESLint errors/warnings. |
| **Build Frontend Bundle** | `npm run build` (in `frontend/`) | Vite production bundle built successfully in `dist/`. |
| **Run Database Migrations**| `uv run alembic upgrade head` | Applies 16 schema migrations to PostgreSQL. |
| **Start Local Container Stack**| `docker compose up -d` | Boots local PostgreSQL and Keycloak containers (FastAPI and Nginx are run separately via Dockerfile). |

---

## Appendix F: LLM Planner Output Schema Structure

When interpreting user natural language requests, the LLM planner returns structured JSON validated by Pydantic models in `src/agent_system/domain/orchestration.py`.

```json
{
  "command": "search_flights",
  "language": "vi",
  "plan": "User wants flights from Hanoi to Singapore next Tuesday under 5 million VND.",
  "dialogue_act": "inform_search_criteria",
  "interpreted_destination": "Singapore",
  "destination_scope": "city",
  "semantic_updates": {
    "origin": { "raw_text": "Hanoi", "normalized": "hanoi", "confidence": 0.98 },
    "destination": { "raw_text": "Singapore", "normalized": "singapore", "confidence": 0.99 },
    "temporal": { "expression_type": "relative", "raw_expression": "next Tuesday", "relative_offset_days": 7 },
    "budget": { "amount": 5000000.0, "currency": "VND" },
    "optimization": { "preference": "cheapest" }
  }
}
```

This structured output schema guarantees that natural language parsing is fully validated by Pydantic before any downstream backend service executes airport lookups, date calculations, or provider searches.
