import { Navigate, Route, Routes } from 'react-router-dom'
import { GuestOnly, Layout, RequireAuth } from './components/Layout'
import { AdminPage, HistoryPage, NewJobPage, ProfilePage } from './pages/AppPages'
import { ForgotPasswordPage, LoginPage, ResetPasswordPage, SignupPage, VerifyPage } from './pages/AuthPages'
import { SessionPage } from './pages/SessionPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route element={<GuestOnly />}>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/signup" element={<SignupPage />} />
          <Route path="/verify" element={<VerifyPage />} />
          <Route path="/forgot" element={<ForgotPasswordPage />} />
          <Route path="/reset" element={<ResetPasswordPage />} />
        </Route>
        <Route element={<RequireAuth />}>
          <Route path="/" element={<HistoryPage />} />
          <Route path="/new" element={<NewJobPage />} />
          <Route path="/profile" element={<ProfilePage />} />
          <Route path="/sessions/:id" element={<SessionPage />} />
          <Route path="/admin" element={<AdminPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
