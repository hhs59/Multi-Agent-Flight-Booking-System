# Design and Integration of an AI-Assisted Flight Search and Booking System

**Project Technical Report**  
**Author:** Software Engineering Student  
**System Basis:** Backend source code (`src/`), Frontend source code (`frontend/src/`), Database migrations (`alembic/`), Auth0 configuration (`auth0/`), and test suites  
**Python Version:** 3.11–3.12 (`pyproject.toml`) | **Frontend Stack:** React 19, TypeScript, Vite, TanStack Query  

---

## Abstract

This report presents the design and integration of an AI-assisted flight search and booking system. The system combines a conversational user interface with deterministic backend services. Users can express travel destinations, travel dates, budgets, preferences, and booking requests in natural language. A large language model (LLM) is used to interpret user messages and output a constrained planning object. Deterministic backend logic is responsible for resolving airport locations, calculating absolute dates, converting currencies, calling external flight providers, validating input, saving data to PostgreSQL, and managing booking state transitions. This division prevents the LLM from inventing fake airport codes, prices, booking references, or payment outcomes.

The implementation consists of a Python FastAPI backend with PostgreSQL persistence and a React/TypeScript frontend. Orchestration is managed using a LangGraph state graph that routes conversational turns across planning, context loading, flight search, trip discovery, destination inspiration, weather enrichment, place recommendations, booking, price watches, user profiles, and clarification nodes. The LLM integration uses an OpenAI-compatible adapter supporting remote models such as DeepSeek. External API integrations include Duffel for flight search and sandbox order creation, OpenWeather for optional forecast data, and a curated place recommendations dataset. Authentication uses OpenID Connect (Auth0/Keycloak) exchanged for secure backend session cookies with CSRF protection and encrypted traveler personal data.

The backend verification suite contains 605 passed, 16 skipped, and 2 deselected tests under default pytest configuration, while the frontend suite contains 100 passed tests across 19 test files. This report details the requirements, system architecture, database schema, integration workflows, software verification evidence, and explicit limitations of the implemented project.

**Keywords:** conversational AI, flight search, task-oriented dialogue, LangGraph, Duffel API, OpenID Connect, PostgreSQL, FastAPI, React, booking workflow.

---

## Evidence and Scope Policy

To maintain technical accuracy, this report distinguishes three categories of claims:

1. **Implemented behavior:** Features verified directly by existing source code, database migrations, API routes, and unit/integration tests.
2. **Configuration-dependent behavior:** Capabilities that depend on environment settings, such as provider API keys, feature flags, or external service endpoints.
3. **Future work or limitations:** Features that are not implemented or tested, described clearly as future development rather than completed results.

This report does not claim that the project trained a custom AI model, conducted live production payments, issued real airline tickets, or completed a formal human usability study. Test counts reported represent automated software contract verification rather than model accuracy benchmarks or live API reliability metrics.

---

## Table of Contents

- [Chapter 1: Introduction and Problem Statement](#chapter-1-introduction-and-problem-statement)
  - [1.1 Background](#11-background)
  - [1.2 Problem Statement](#12-problem-statement)
  - [1.3 Project Objectives](#13-project-objectives)
  - [1.4 Scope and Limitations](#14-scope-and-limitations)
  - [1.5 Report Structure](#15-report-structure)
- [Chapter 2: Theoretical Foundations](#chapter-2-theoretical-foundations)
  - [2.1 Conversational AI](#21-conversational-ai)
  - [2.2 Large Language Models](#22-large-language-models)
  - [2.3 Structured Output and Schema Validation](#23-structured-output-and-schema-validation)
  - [2.4 Task-Oriented Dialogue](#24-task-oriented-dialogue)
  - [2.5 Flight Search and Booking APIs](#25-flight-search-and-booking-apis)
  - [2.6 Authentication and Security Concepts](#26-authentication-and-security-concepts)
  - [2.7 Technologies Used](#27-technologies-used)
- [Chapter 3: System Analysis and Design](#chapter-3-system-analysis-and-design)
  - [3.1 Stakeholders](#31-stakeholders)
  - [3.2 Functional Requirements](#32-functional-requirements)
  - [3.3 Non-functional Requirements](#33-non-functional-requirements)
  - [3.4 Use Cases](#34-use-cases)
  - [3.5 System Architecture](#35-system-architecture)
  - [3.6 Main Components](#36-main-components)
  - [3.7 Database Design](#37-database-design)
  - [3.8 Conversation and Context Management](#38-conversation-and-context-management)
  - [3.9 Flight Search](#39-flight-search)
  - [3.10 Recommendation and Weather Features](#310-recommendation-and-weather-features)
  - [3.11 Booking Workflow](#311-booking-workflow)
  - [3.12 Security Design](#312-security-design)
  - [3.13 Error Handling](#313-error-handling)
  - [3.14 Persistence and Idempotency](#314-persistence-and-idempotency)
- [Chapter 4: System Implementation and Integration](#chapter-4-system-implementation-and-integration)
  - [4.1 Backend](#41-backend)
  - [4.2 Frontend](#42-frontend)
  - [4.3 LLM Integration](#43-llm-integration)
  - [4.4 Flight Provider Integration](#44-flight-provider-integration)
  - [4.5 Weather Integration](#45-weather-integration)
  - [4.6 Authentication](#46-authentication)
  - [4.7 Database](#47-database)
  - [4.8 Booking Integration](#48-booking-integration)
  - [4.9 Deployment Configuration](#49-deployment-configuration)
- [Chapter 5: Testing and Evaluation](#chapter-5-testing-and-evaluation)
  - [5.1 Testing Strategy](#51-testing-strategy)
  - [5.2 Backend Testing](#52-backend-testing)
  - [5.3 Frontend Testing](#53-frontend-testing)
  - [5.4 Integration Testing](#54-integration-testing)
  - [5.5 Booking and Security Testing](#55-booking-and-security-testing)
  - [5.6 Results](#56-results)
  - [5.7 Limitations](#57-limitations)
  - [5.8 Threats to Validity](#58-threats-to-validity)
- [Chapter 6: Conclusion and Future Work](#chapter-6-conclusion-and-future-work)
  - [6.1 Conclusion](#61-conclusion)
  - [6.2 Future Work](#62-future-work)
- [References](#references)
- [Appendices](#appendices)
  - [Appendix A: Source Code & Implementation Evidence Mapping](#appendix-a-source-code--implementation-evidence-mapping)
  - [Appendix B: Database Schema & Entity Quick Reference (24 Model Classes)](#appendix-b-database-schema--entity-quick-reference-24-model-classes)
  - [Appendix C: System REST API Endpoints Reference](#appendix-c-system-rest-api-endpoints-reference)
  - [Appendix D: System Configuration & Environment Variables](#appendix-d-system-configuration--environment-variables)
  - [Appendix E: System Verification & Execution Commands](#appendix-e-system-verification--execution-commands)
  - [Appendix F: LLM Planner Output Schema Structure](#appendix-f-llm-planner-output-schema-structure)

---

# Chapter 1: Introduction and Problem Statement

## 1.1 Background

Booking a flight usually requires travelers to fill out structured forms with exact details: origin airport code, destination airport code, departure date, return date, passenger counts, cabin class, and budget limits. Traditional booking interfaces work well when users already know these details. However, they struggle when users have flexible or incomplete travel plans, such as *"I have 5 million VND, where can I travel next week?"* or *"Find me a cheap flight from Hanoi to Singapore next month."*

A conversational assistant allows users to express their goals in flexible natural language, ask questions, and refine search criteria step by step. However, building a travel assistant introduces technical challenges. Flight availability, ticket prices, IATA airport codes, offer validity periods, traveler records, and booking transactions must be completely accurate. Language models can understand natural language well, but they can also hallucinate facts, make up non-existent flight numbers, or invent fake prices. Therefore, a flight booking system cannot rely solely on an LLM to manage travel data or execute transactions.

To solve this problem, this project uses a hybrid architecture. Natural language understanding is handled by a language model, while domain validation, location resolution, flight offer searches, state management, database storage, and booking workflows are handled entirely by deterministic backend code.

## 1.2 Problem Statement

This project addresses the following primary engineering problem:

> How to design and implement a conversational flight search and booking system that accurately interprets flexible natural language requests, while ensuring that all flight data, location resolutions, date calculations, price comparisons, and booking state transitions remain under strict deterministic backend control.

To solve this problem effectively, the system must address five specific technical challenges:

1. **Natural Language Ambiguity:** Users may mention city names instead of airport codes, specify relative dates like *"next Friday"*, make spelling mistakes, or mix English and Vietnamese.
2. **External Data Volatility:** Flight offers expire quickly, prices change dynamically, and external API providers may be temporarily unavailable.
3. **Multi-Turn Continuity:** Follow-up requests like *"show me the cheapest one"* or *"book option 2"* depend on results returned in earlier conversation turns.
4. **Transaction Safety:** Flight searches are read-only operations, but creating a booking changes external provider states. Booking requires valid traveler profiles, fresh price quotes, explicit user confirmation, and idempotency protection to prevent duplicate orders.
5. **Operational Resilience:** Failures in optional third-party services (such as weather forecasts or place recommendations) must not break core flight search results.

## 1.3 Project Objectives

The main objectives of this project are:

1. Implement a web-based conversational interface for flight discovery, search, recommendations, price tracking (watches), user profile management, and booking workflows.
2. Integrate an LLM provider using structured output validation to extract user intent without allowing the model to generate flight data or modify system state directly.
3. Build a location resolution service that maps user location input (including misspelled or diacritic-free text) to valid IATA airport codes.
4. Implement a server-side date resolver that calculates exact date windows from relative expressions based on a fixed travel timezone.
5. Integrate an external flight API (Duffel) to retrieve live flight offers and create sandbox bookings.
6. Provide destination inspiration by matching verified flight fares against user budget constraints.
7. Support optional destination weather forecasts (OpenWeather) and curated place recommendations without letting external API errors block flight search results.
8. Store conversation history, flight searches, user preferences, quotes, and booking records reliably using PostgreSQL.
9. Implement authentication via OpenID Connect (OIDC), secure session management, CSRF token validation, and encryption for sensitive traveler data.
10. Verify system behavior using automated backend and frontend test suites.

## 1.4 Scope and Limitations

### Implemented Scope
- **Backend:** Python 3.11–3.12 application using FastAPI, Pydantic, SQLAlchemy 2, Alembic migrations, and LangGraph orchestration.
- **Frontend:** Single-page web application built with React 19, TypeScript, Vite, TanStack Query, and React Router v7.
- **LLM Integration:** OpenAI-compatible API adapter configured for remote LLM execution (such as DeepSeek models).
- **Flight API:** Duffel flight offer search, offer repricing/quote preparation, and sandbox order creation.
- **Enrichment APIs:** OpenWeather API integration for destination weather forecasts, and curated place recommendation data.
- **Security & Storage:** Auth0/Keycloak OIDC authentication, secure cookie session storage, CSRF token validation, AES encryption for traveler PII, and 24 database model tables in PostgreSQL.

### Explicit Project Limitations
- The project does not train or fine-tune custom AI models.
- The system does not issue real production tickets or process live monetary credit card payments (order creation is restricted to Duffel sandbox environments).
- Budget filtering applies strictly to airfare; it does not compute complete trip expenses such as hotel accommodation, food, or local transport.
- Benchmark accuracy for natural language intent classification or typo correction was not measured against a formal public dataset.
- Automated tests verify software contracts and API integrations using mock fixtures and sandbox endpoints; they do not measure live model accuracy or human user satisfaction.

## 1.5 Report Structure

The remainder of this report is organized as follows:
- **Chapter 2 (Theoretical Foundations)** explains conversational AI, task-oriented dialogue systems, structured output validation, hybrid control, and application security concepts.
- **Chapter 3 (System Analysis and Design)** presents stakeholders, functional requirements, use cases, high-level architecture, database schema, and component designs.
- **Chapter 4 (System Implementation and Integration)** details how FastAPI, React, LangGraph, Duffel API, OpenWeather, Auth0, and PostgreSQL are integrated.
- **Chapter 5 (Testing and Evaluation)** documents test methodologies, software verification outcomes, frontend build verification, and explicit limitations.
- **Chapter 6 (Conclusion and Future Work)** summarizes project contributions and outlines future software enhancements.
- **Appendices (Technical Quick Reference)** provides detailed reference tables mapping source code evidence, database entities, REST APIs, environment variables, execution commands, and JSON schemas for easy retrieval.

---

# Chapter 2: Theoretical Foundations

## 2.1 Conversational AI

Conversational AI focuses on building software that enables human-like interactions between computers and users through voice or text. In travel software, conversational interfaces allow users to explain complex constraints—such as flexible dates, destination preferences, and budget limits—in natural language rather than through multiple dropdown menus.

Traditional rule-based dialogue interfaces rely on strict patterns and fixed forms. They often break when users phrase requests in unexpected ways or omit required fields. Modern conversational AI uses statistical and neural language models to handle language variations while maintaining context across multi-turn interactions.

## 2.2 Large Language Models

Large Language Models (LLMs) are deep learning models trained on broad textual datasets. They excel at text interpretation, entity extraction, sentiment analysis, and multi-turn dialogue management. In this application, the LLM is used as an intent interpreter.

The LLM processes user messages, identifies requested travel actions, and extracts key semantic parameters such as cities, dates, and budgets. The LLM is configured via prompt engineering and JSON response constraints to ensure it acts as an interpreter rather than a factual data generator. This design prevents the LLM from fabricating airport codes, flight availability, or ticket prices.

## 2.3 Structured Output and Schema Validation

Unstructured text output from a language model is hard to parse safely in web applications. To enforce reliability, the system uses structured output validation backed by Pydantic schemas.

When sending requests to the LLM, the backend specifies a strict JSON schema for the response. The model returns structured fields including command type, conversation language, dialogue act, and semantic updates. If the model returns malformed JSON or violates the required schema, the backend validation layer catches the exception and safely falls back to a controlled clarification prompt.

## 2.4 Task-Oriented Dialogue

A task-oriented dialogue system helps users complete specific actions, such as searching for flights or booking a ticket. Unlike open-ended conversational chatbots, a task-oriented assistant maintains explicit domain state across dialogue turns.

In flight booking, the required task state includes origin, destination, departure date, return date, passenger count, cabin class, and budget limit. The system tracks which fields have been provided, asks for missing mandatory details, and invokes backend APIs only when sufficient valid parameters exist.

## 2.5 Flight Search and Booking APIs

Flight search applications connect to external Aggregators or Global Distribution Systems (GDS). This system integrates with the Duffel API, a modern flight distribution platform.

Key concepts in flight API integration include:
- **Offer Request:** A search query specifying origin, destination, dates, passenger count, and cabin class.
- **Offer:** A specific flight itinerary returned with a fixed fare, currency, airline breakdown, baggage policy, and offer expiration timestamp.
- **Quote / Reprice:** Verifying that an offer price and seat availability remain valid before proceeding to purchase.
- **Order Creation:** Submitting passenger profiles and payment settings to confirm ticket booking.

Because flight prices fluctuate frequently, an offer shown to a user remains valid only for a limited duration. The system re-verifies price quotes prior to booking execution.

## 2.6 Authentication and Security Concepts

Applications handling user profile data and booking transactions require strict security controls:
- **OpenID Connect (OIDC):** An identity layer built on top of OAuth 2.0. Users authenticate with an identity provider (e.g., Auth0 or Keycloak), which issues verified ID and access tokens.
- **Session Cookie Authentication:** The backend exchanges validated OIDC tokens for an encrypted, HTTP-only session cookie (`session_token`), protecting tokens from client-side script access.
- **Cross-Site Request Forgery (CSRF) Protection:** State-changing requests (such as profile edits or booking creation) require a valid `X-CSRF-Token` header.
- **Personally Identifiable Information (PII) Encryption:** Sensitive traveler data (passport numbers, dates of birth, phone numbers) are encrypted at rest in PostgreSQL using AES-256-GCM authenticated encryption (`AESGCM`).

## 2.7 Technologies Used

The core technology stack consists of:
- **Python 3.11–3.12 & FastAPI:** Modern asynchronous Web API framework.
- **Pydantic v2:** Fast data validation library using Python type hints.
- **LangGraph v0.2:** State graph framework for orchestrating dialogue agent workflows.
- **SQLAlchemy 2 & Alembic:** SQL ORM toolkit and schema migration management.
- **PostgreSQL:** Durable relational database system.
- **React 19 & TypeScript:** Frontend web application framework with static typing.
- **TanStack Query (React Query v5):** Asynchronous state management for API fetching, caching, and state synchronization.
- **Vite:** High-performance frontend build tool.

---

# Chapter 3: System Analysis and Design

## 3.1 Stakeholders

- **End Users:** Travelers using the web app to search for flights, inspect recommendations, set price watches, and complete sandbox booking orders.
- **Software Developers & Operators:** Engineers deploying backend services, configuring API keys (Duffel, OpenWeather, Auth0, LLM), monitoring log traces, and managing database schema migrations.
- **Academic Evaluators:** Reviewers assessing software architecture, engineering decisions, codebase organization, and verification evidence.

## 3.2 Functional Requirements

The functional capabilities implemented in the project are summarized in Table 3.1.

*Table 3.1: Functional Requirements*

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
| **FR-16** | The system encrypts sensitive traveler PII in the database. | Implemented |

## 3.3 Non-functional Requirements

- **Reliability:** Third-party API failures (e.g., weather or place recommendation outages) must degrade gracefully without crashing core flight search workflows.
- **Security:** State-changing API endpoints require authenticated sessions and valid CSRF token headers. Sensitive fields are encrypted at rest.
- **Performance:** Location resolution uses in-memory airport caching. Provider requests use bounded HTTP timeouts (e.g., 10s for Duffel searches).
- **Maintainability:** Clear separation between API route handlers, domain services, provider adapters, and database repositories.

## 3.4 Use Cases

### UC-01: Conversational Flight Search
1. The user enters a message in the chat interface (e.g., *"Find flights from Hanoi to Da Nang next Tuesday"*).
2. The frontend sends the payload to `/api/v1/threads/{thread_id}/messages`.
3. The LLM planner extracts origin (*Hanoi*), destination (*Da Nang*), and relative date (*next Tuesday*).
4. The location resolver maps *Hanoi* → `HAN` and *Da Nang* → `DAD`.
5. The date resolver calculates *next Tuesday* into an exact ISO date string (e.g., `2026-08-25`).
6. The flight service queries the Duffel API and retrieves available offers.
7. Offers are ranked, stored in PostgreSQL, and returned as structured UI offer cards.

### UC-02: Destination Inspiration within Budget
1. The user asks: *"Where can I fly from Ho Chi Minh City with a budget of 3,000,000 VND?"*
2. The LLM planner identifies the command as `trip_inspiration` with origin `SGN` and budget `3,000,000 VND`.
3. The system selects candidate destinations (`DAD`, `PQC`, `BKK`, `SIN`), searches Duffel for available fares, and applies currency conversion.
4. Valid destinations with airfares under 3,000,000 VND are returned as inspiration cards.
5. Clicking an inspiration card opens a detailed flight search for that specific route.

### UC-03: Direct Search via Flights Form
1. The user navigates to the **Flights** tab.
2. The user fills out origin (`HAN`), destination (`SIN`), departure date (`2026-09-01`), passenger counts, and cabin class.
3. The frontend submits a POST request to `/api/v1/flights/search`.
4. The backend searches Duffel and renders matching offer cards.

### UC-04: Sandbox Flight Booking Confirmation
1. The user selects a flight offer card and clicks **Book Flight**.
2. The frontend opens the booking modal and prompts selection of a Traveler Profile.
3. The backend checks offer quote freshness and verifies traveler profile completeness.
4. The user clicks **Confirm Sandbox Booking**.
5. The backend attaches an idempotency key and submits the order request to Duffel's sandbox endpoint.
6. Duffel returns a sandbox order reference (e.g., `ord_0000Axxx`).
7. The backend creates a local booking record with status `order_created` and displays order confirmation details.

## 3.5 System Architecture

The system architecture follows a multi-tier structure as shown in Figure 3.1.

```
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
*Figure 3.1: System Architecture Diagram*

## 3.6 Main Components

The backend inside `src/agent_system/` is organized into modular packages:
- `api/`: FastAPI route handlers (`product.py`, `operations.py`, `auth/router.py`).
- `auth/`: OIDC token validation (`oidc.py`), session storage (`sessions.py`), and authentication middleware.
- `domain/`: Pure domain logic models (`flights.py`, `booking_workflow.py`, `location_resolution.py`).
- `services/`: Business services for orchestration, flight search, ranking, date parsing, location lookup, and booking workflow gates.
- `providers/`: External API adapters (`duffel/`, `openweather/`, `llm_providers.py`, `places.py`).
- `db/`: Database session setup (`session.py`), base mixins (`base.py`), and ORM models (`models.py`).
- `security/`: Encryption utilities (`encryption.py`) and safe data sanitization (`safe_results.py`).

## 3.7 Database Design

The database schema is defined in `src/agent_system/db/models.py` and managed via Alembic migrations. It contains **24 database table models** across 6 functional areas:

1. **Identity & User Profiles:** `UserRecord`, `UserSessionRecord`, `TravelerProfileRecord`, `UserTravelPreferenceRecord`.
2. **Conversation & Assistant State:** `ChatThreadRecord`, `ChatMessageRecord`, `AgentCheckpointRecord`.
3. **Flight Search & Discovery:** `FlightSearchRecord`, `FlightOfferRecord`, `FlightDiscoveryRecord`, `FlightSearchAttemptRecord`.
4. **Booking & Orders:** `BookingQuoteRecord`, `BookingIntentRecord`, `BookingRecord`, `BookingEventRecord`, `BookingOperationRecord`, `PurchaseMandateRecord`.
5. **Price Alert Watches:** `FlightWatchRecord`, `WatchRunRecord`, `WatchMatchRecord`, `WatchHoldRecord`, `WatchNotificationRecord`.
6. **Operations & Audit Logs:** `AuditEventRecord`, `OutboxEventRecord`.

## 3.8 Conversation and Context Management

The LangGraph orchestration engine (`orchestration_graph.py`) manages dialogue state across graph nodes. A conversation turn executes through the following path:

```
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
*Figure 3.2: Conversation Turn Processing Sequence*

## 3.9 Flight Search

- **Search Execution (`flight_search.py`):** Converts validated search criteria into Duffel API payloads, issues asynchronous HTTP requests, and normalizes raw JSON offers into internal models.
- **Offer Ranking (`flight_ranking.py`):** Sorts offers based on explicit user criteria:
  - `cheapest`: Sort by price ascending.
  - `fastest`: Sort by flight duration ascending.
  - `fewest_stops`: Sort by number of connections ascending.

## 3.10 Recommendation and Weather Features

- **Weather Forecasts (`weather.py`):** Queries 3-day forecasts for destination coordinates via OpenWeather. If the weather service fails, the backend marks weather status as `unavailable` without blocking flight search results.
- **Destination Places (`places.py` & `destination_recommendations.py`):** Loads curated points of interest from `curated_destinations.v1.json` for major cities. Entries include explicit `source` labels (e.g., `curated_v1`) to distinguish database records from LLM descriptions.

## 3.11 Booking Workflow

To ensure transaction safety, `booking_workflow.py` enforces 6 safety gates before order submission:
1. **Feature Flag Check:** Confirm booking functionality is enabled in settings.
2. **Offer Ownership:** Confirm the selected offer belongs to the user's search history.
3. **Quote Freshness:** Re-verify offer validity and price stability.
4. **Traveler Completeness:** Ensure mandatory passenger details (full name, DOB, gender, email, passport) are present.
5. **Idempotency Key:** Attach a unique idempotency key (`user_id + offer_id + attempt`) to prevent duplicate bookings.
6. **Explicit User Confirmation:** Require explicit user confirmation in the UI modal before sending order requests to Duffel.

## 3.12 Security Design

- **Session Cookies:** OIDC JWTs are verified backend-side. The system issues an encrypted, HTTP-only session cookie (`session_token`).
- **CSRF Token:** State-changing requests require matching `X-CSRF-Token` headers.
- **PII Encryption:** Sensitive traveler fields in `TravelerProfileRecord` are encrypted at rest using AES-256-GCM authenticated encryption (`AESGCM`).
- **CORS Configuration:** Restricts cross-origin requests strictly to configured frontend domains (`http://localhost:5173`).

## 3.13 Error Handling

The application maps internal errors to safe HTTP status codes:
- Validation errors → `400 Bad Request`
- Expired or changed quotes → `409 Conflict` (prompts offer re-search)
- Provider timeouts → `504 Gateway Timeout`
- Internal exceptions → `500 Internal Server Error` (returns trace ID without leaking stack traces)

## 3.14 Persistence and Idempotency

All system data is stored durably in PostgreSQL. Idempotency protection prevents duplicate side effects when users resubmit forms or retry network calls. Turn processing locks enforce single-turn execution per conversation thread.

---

# Chapter 4: System Implementation and Integration

## 4.1 Backend

The backend is built with Python 3.11–3.12 and FastAPI. Routes in `src/agent_system/api/product.py` expose endpoints for authentication, conversation threads, flight searching, traveler profiles, booking orders, and price watches.

## 4.2 Frontend

The frontend is a single-page application built with React 19, TypeScript, and Vite (`frontend/src/`). The layout (`AppShell.tsx`) provides navigation across four main pages:
1. **Assistant Page (`AssistantPage.tsx`):** Interactive chat interface displaying natural language messages, structured offer cards, weather widgets, and place recommendations.
2. **Flights Page (`SearchPage.tsx`):** Form interface for direct flight searches.
3. **Bookings Page (`BookingsPage.tsx`):** Overview of saved booking intents, sandbox order statuses, and quotes.
4. **Travelers Page (`TravelersPage.tsx`):** Interface for managing traveler profiles with completeness validation indicators.

## 4.3 LLM Integration

The LLM adapter (`src/agent_system/llm_providers.py`) communicates with OpenAI-compatible APIs (such as DeepSeek) via `httpx`.

Requests use temperature `0.0` and structured JSON response schemas:
```json
{
  "model": "deepseek-chat",
  "temperature": 0.0,
  "response_format": { "type": "json_object" },
  "messages": [
    { "role": "system", "content": "You are a flight assistant planner..." },
    { "role": "user", "content": "{'message': 'Find flights to Bangkok next Friday'}" }
  ]
}
```
If the LLM times out or returns invalid JSON, `LLMOutputError` or `LLMUnavailableError` exceptions trigger safe clarification fallbacks.

## 4.4 Flight Provider Integration

The Duffel provider adapter (`src/agent_system/providers/duffel/`) handles API integration:
- `client.py`: Issues HTTP requests with `Authorization: Bearer <token>` and `Duffel-Version: v2` headers, handling rate limit retries (HTTP 429) and timeouts.
- `flights.py`: Converts internal queries into Duffel `/air/offer_requests` payloads and parses returned flight itineraries and pricing.
- `orders`: Submits sandbox booking orders to `/air/orders` using `balance` payment methods.

## 4.5 Weather Integration

- **OpenWeather (`providers/openweather/`):** Queries 3-day destination forecasts and caches responses in memory for 3 hours. Weather failures degrade gracefully without affecting flight search outputs.
- **Curated Places (`providers/places.py`):** Loads place recommendations from `curated_destinations.v1.json` for major destinations.

## 4.6 Authentication

The OIDC authentication flow works as follows:
1. User clicks **Login**, redirecting to Auth0/Keycloak.
2. After authenticating, the identity provider redirects to `/auth/callback` with an authorization code.
3. The frontend posts the code to backend `/api/v1/auth/session`.
4. The backend verifies the token, creates user and session records in PostgreSQL, and sets an encrypted HTTP-only session cookie.

## 4.7 Database

Database persistence relies on SQLAlchemy 2 async sessions and Alembic schema migrations (`alembic/versions/`). 16 migration scripts establish table structures, foreign key relationships, constraints, and indexes.

## 4.8 Booking Integration

The booking execution workflow follows a clear sequence:
1. User clicks **Book Flight** on an offer card.
2. Backend creates a draft `BookingIntentRecord` and re-verifies quote pricing.
3. User selects a complete Traveler Profile and clicks **Confirm Sandbox Booking**.
4. Backend submits the order to Duffel's sandbox endpoint.
5. Upon response, backend saves a `BookingRecord` with status `order_created` and Duffel order reference code.

## 4.9 Deployment Configuration

Container deployment configuration includes:
- **Backend Dockerfile:** Multi-stage Python 3.11 slim image running Alembic migrations and Uvicorn.
- **Frontend Dockerfile:** Builds static Vite bundle (`dist/`) served via Nginx with SPA routing fallback (`nginx.conf.template`).
- **Docker Compose (`compose.yaml`):** Orchestrates PostgreSQL, Keycloak dev container, FastAPI backend, and Nginx frontend.

---

# Chapter 5: Testing and Evaluation

## 5.1 Testing Strategy

The system verification strategy combines backend unit/integration tests, frontend component tests, and build checks:
1. **Backend Tests (Pytest):** Tests domain logic, date parsing, location resolution, LLM planner schema parsing, Duffel API mapping, booking gates, and security middleware.
2. **Frontend Tests (Vitest):** Tests UI components, custom hooks, API services, booking dialogs, and route protection.
3. **Code Quality Checks:** Static type checking (`tsc`), ESLint validation, and Ruff Python linting.

## 5.2 Backend Testing

Executing backend tests (`uv run pytest -q`) under default configuration (`-m 'not provider'`) yields:
```text
605 passed, 16 skipped, 2 deselected, 2 warnings in 47.25s
```
*Note: Provider-marked tests (`-m provider`) require live sandbox API credentials and network access, so they are excluded from default offline test runs.*

Focused backend test group results are summarized in Table 5.1.

*Table 5.1: Backend Test Suite Verification Breakdown*

| Test Suite Category | Included Test Files | Outcome | Tested Capabilities |
|---|---|---|---|
| **Conversational & Semantic** | `test_deepseek_semantic_updates.py`, `test_phase1_trip_discovery.py`, `test_turn_language.py`, `test_chat_memory.py` | 110 Passed | Planner schema parsing, relative date math, language selection, typo handling, dialogue memory context. |
| **Search, Ranking & Weather** | `test_exchange_rates.py`, `test_phase5_destination_recommendations.py`, `test_openweather_and_services.py`, `test_trip_inspiration.py` | 127 Passed, 1 Skipped | Currency conversion, candidate search, offer ranking, place catalog, weather fallback. |
| **Booking & Security** | `test_phase6_booking_workflow.py`, `test_phase8_duffel_balance_booking.py`, `test_security_phase2.py`, `test_auth_phase2.py` | 35 Passed | Quote verification, traveler completeness, sandbox order creation, OIDC validation, PII encryption. |

## 5.3 Frontend Testing

Frontend verification commands (`npm test`, `npm run lint`, `npm run build`) produce:
```text
Vitest Run: 19 test files passed, 100 tests passed (18.66s)
ESLint: 0 errors, 0 warnings
Vite Build: 2,192 modules transformed, dist/ index bundle built successfully
```

## 5.4 Integration Testing

Integration tests verify component interactions:
- LLM output parsing to location resolution and Duffel API payload generation.
- Session cookie verification and CSRF token checking on API mutation routes.
- Exception handling when OpenWeather or place recommendation services are unconfigured.

## 5.5 Booking and Security Testing

- **Traveler Validation:** Verified that attempting to book an offer with incomplete traveler details returns a `traveler_incomplete` validation error before calling Duffel APIs.
- **Sandbox Order Execution:** Verified that confirming a booking with valid traveler details generates a Duffel sandbox order reference (`ord_...`) and saves a local `BookingRecord` with status `order_created`.
- **Security Controls:** Verified that endpoints reject requests missing valid session cookies (HTTP 401) or CSRF tokens (HTTP 403).

## 5.6 Results

Backend tests (605 passed) and frontend tests (100 passed across 19 files) demonstrate that the software implementation meets specified functional, security, and workflow requirements.

## 5.7 Limitations

To maintain academic honesty, the following limitations are explicitly noted:
1. **No Fine-Tuned Model Benchmarks:** The project did not train a custom LLM or publish formal intent-accuracy metrics against public datasets (e.g., ATIS or MultiWOZ).
2. **Offline Unit Test Scope:** Pytest runs use mock provider responses and fixture payloads for speed and reproducibility. Passing tests verify software logic, not 100% live API availability.
3. **Sandbox Order Execution:** Duffel booking execution was tested exclusively against Duffel's test sandbox. The system does not process live payments or issue production airline tickets.
4. **Airfare-Only Budgeting:** Budget filtering applies strictly to airfares; lodging, food, and local activity costs are not calculated.
5. **No Human Usability Study:** Usability conclusions are based on component testing rather than formal participant user studies.

## 5.8 Threats to Validity

- **LLM Output Variability:** Remote LLMs may occasionally return unexpected output formatting not observed during offline testing.
- **Provider API Volatility:** Real-world flight prices and seat availability change rapidly; mock provider tests cannot predict live fare availability.
- **Environment Configuration:** Automated tests execute against SQLite and local test databases; production deployments on PostgreSQL require proper environment variable configuration.

---

# Chapter 6: Conclusion and Future Work

## 6.1 Conclusion

This project successfully designed, implemented, and evaluated an AI-assisted flight search and booking system combining conversational interface capabilities with deterministic backend domain controls.

The primary contribution of this work is its **hybrid control architecture**:
- A remote LLM (via OpenAI-compatible API) interprets natural language user requests, tracks multi-turn conversation context, and extracts structured travel parameters.
- Deterministic Python/FastAPI services manage location resolution, relative date calculations, currency conversion, Duffel API searches, PostgreSQL persistence, and booking workflow state transitions.

This strict architectural separation prevents the LLM from inventing fake flight numbers, airport codes, ticket prices, or booking confirmations. The system includes 24 PostgreSQL database tables, robust OIDC authentication, session cookie security, CSRF protection, traveler PII encryption, and responsive frontend UI components. Software verification confirmed that the backend test suite (605 passed tests) and frontend test suite (100 passed tests across 19 files) meet system quality and safety standards.

## 6.2 Future Work

Future software enhancements include:
1. **Multilingual Dataset Evaluation:** Collect and annotate a benchmark dataset of Vietnamese and English travel queries to measure LLM intent parsing accuracy formally.
2. **Live Exchange Rate Provider:** Integrate a real-time financial FX rate API to replace static demo exchange rates.
3. **Full Trip Budget Estimation:** Expand trip inspiration features to include lodging and local transport estimates alongside airfares.
4. **Production Payment Gateway:** Integrate a payment gateway (e.g., Stripe) to support real production ticket issuance.
5. **Enhanced Disambiguation UI:** Improve frontend modal dialogs when resolving ambiguous country-level destinations with multiple airports.
6. **Mobile Application Development:** Build a dedicated mobile client (React Native) consuming the existing REST API backend.

---

# References

1. Auth0. *Authorization Code Flow with PKCE Documentation*. Available online: `https://auth0.com/docs/get-started/authentication-and-authorization-flow/authorization-code-flow`
2. Duffel Financial Ltd. *Duffel Flight API v2 Reference Documentation*. Available online: `https://duffel.com/docs/api`
3. FastAPI Framework. *FastAPI Official Documentation*. Available online: `https://fastapi.tiangolo.com/`
4. LangChain / LangGraph. *LangGraph Orchestration & State Graph Documentation*. Available online: `https://langchain-ai.github.io/langgraph/`
5. OpenWeather Ltd. *OpenWeather Current Weather and Forecast API*. Available online: `https://openweathermap.org/api`
6. PostgreSQL Global Development Group. *PostgreSQL 16 Documentation*. Available online: `https://www.postgresql.org/docs/16/index.html`
7. Pydantic. *Pydantic Data Validation Documentation*. Available online: `https://docs.pydantic.dev/`
8. React Core Team. *React 19 Documentation*. Available online: `https://react.dev/`

---

# Appendices

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
| **E-12** | `src/agent_system/security/encryption.py` | AES-256-GCM (`AESGCM`) cryptographic protection for sensitive traveler PII database fields. |
| **E-13** | `frontend/src/pages/AssistantPage.tsx` | React 19 natural language chat page with structured offer cards and enrichment widgets. |
| **E-14** | `frontend/src/pages/SearchPage.tsx` | React 19 direct flight search form page with origin, destination, date, and passenger inputs. |
| **E-15** | `frontend/src/pages/BookingsPage.tsx` | React 19 booking management overview page showing sandbox orders, confirmation codes, and quotes. |
| **E-16** | `frontend/src/pages/TravelersPage.tsx` | React 19 traveler profile management page with readiness completeness indicators. |
| **E-17** | `Dockerfile` & `frontend/Dockerfile` | Production Docker container build manifests for backend Uvicorn server and frontend Nginx web server. |
| **E-18** | `pyproject.toml` | Python project configuration, system dependencies (`FastAPI`, `LangGraph`, `SQLAlchemy`), pytest options. |

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
| **Start Local Container Stack**| `docker compose up -d` | Boots local PostgreSQL, Keycloak, FastAPI, and Nginx. |

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
    "origin": {
      "raw_text": "Hanoi",
      "normalized": "hanoi",
      "confidence": 0.98
    },
    "destination": {
      "raw_text": "Singapore",
      "normalized": "singapore",
      "confidence": 0.99
    },
    "temporal": {
      "expression_type": "relative",
      "raw_expression": "next Tuesday",
      "relative_offset_days": 7
    },
    "budget": {
      "amount": 5000000.0,
      "currency": "VND"
    },
    "optimization": {
      "preference": "cheapest"
    }
  }
}
```

This structured output schema guarantees that natural language parsing is fully validated by Pydantic before any downstream backend service executes airport lookups, date calculations, or provider searches.
