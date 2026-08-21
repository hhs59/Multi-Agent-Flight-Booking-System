import { Navigate, Route, Routes } from 'react-router-dom'
import { RequireAuth } from '../auth/RequireAuth'
import { AppShell } from '../layouts/AppShell'
import { AssistantPage } from '../pages/AssistantPage'
import { AuthCallbackPage, SilentCallbackPage } from '../pages/AuthCallbackPage'
import { BookingDetailPage, BookingsPage } from '../pages/BookingsPage'
import { BookingIntentPage } from '../pages/BookingIntentPage'
import { LoginPage } from '../pages/LoginPage'
import { NotFoundPage } from '../pages/NotFoundPage'
import { OperationsPage } from '../pages/OperationsPage'
import { SearchPage } from '../pages/SearchPage'
import { TravelersPage } from '../pages/TravelersPage'
import { WatchesPage } from '../pages/WatchesPage'

export function AppRouter() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/auth/callback" element={<AuthCallbackPage />} />
      <Route path="/auth/silent-callback" element={<SilentCallbackPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route path="/" element={<Navigate to="/assistant" replace />} />
          <Route path="/assistant" element={<AssistantPage />} />
          <Route path="/assistant/:threadId" element={<AssistantPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/travelers" element={<TravelersPage />} />
          <Route
            path="/preferences"
            element={<Navigate to="/assistant?panel=preferences" replace />}
          />
          <Route path="/booking-intents/:intentId" element={<BookingIntentPage />} />
          <Route path="/bookings" element={<BookingsPage />} />
          <Route path="/bookings/:bookingId" element={<BookingDetailPage />} />
          <Route path="/watches" element={<WatchesPage />} />
          <Route path="/operations" element={<OperationsPage />} />
          <Route path="/admin" element={<Navigate to="/operations" replace />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
