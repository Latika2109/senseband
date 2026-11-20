import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import { CloudProvider } from './cloud/CloudContext'
import type { CloudApi } from './cloud/types'
import { AppProvider, useApp } from './state/AppContext'
import { CloudLogin, CloudPatient, CloudPatients } from './screens/cloud'
import { Alerts, Dashboard, Settings, Shell } from './screens/live'
import { Assess, Calibrate, GripScreen, PlanScreen, ProgressScreen, RecoveryDetails, RecoveryWear, RecoveryWhere, RehabHome, Session } from './screens/recovery'
import { Duration, Pair, Profile, Program, Purpose, SignalCheckScreen, useNextRoute, Welcome, WearGuide } from './screens/setup'

function Home() { return <Navigate to={useNextRoute()} replace /> }

function RequireStarted({ children }: { children: JSX.Element }) {
  const { p } = useApp()
  const next = useNextRoute()
  return p.started && p.bandUrl ? children : <Navigate to={next} replace />
}

export default function App({ cloudApi }: { cloudApi?: CloudApi | null }) {
  return (
    <AppProvider><CloudProvider api={cloudApi}>
      <HashRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/welcome" element={<Welcome />} />
          <Route path="/cloud/login" element={<CloudLogin />} />
          <Route path="/cloud/patients" element={<CloudPatients />} />
          <Route path="/cloud/patient/:id" element={<CloudPatient />} />
          <Route path="/profile" element={<Profile />} />
          <Route path="/pair" element={<Pair />} />
          <Route path="/program" element={<Program />} />
          <Route path="/purpose" element={<Purpose />} />
          <Route path="/duration" element={<Duration />} />
          <Route path="/wear" element={<WearGuide />} />
          <Route path="/check" element={<SignalCheckScreen />} />
          <Route path="/recovery" element={<Navigate to="/recovery/where" replace />} />
          <Route path="/recovery/where" element={<RecoveryWhere />} />
          <Route path="/recovery/details" element={<RecoveryDetails />} />
          <Route path="/recovery/wear" element={<RecoveryWear />} />
          <Route path="/recovery/calibrate" element={<Calibrate />} />
          <Route path="/recovery/assess" element={<Assess />} />
          <Route path="/recovery/plan" element={<PlanScreen />} />
          <Route path="/rehab/session/:id" element={<Session />} />
          <Route path="/rehab/grip" element={<GripScreen />} />
          <Route element={<RequireStarted><Shell /></RequireStarted>}>
            <Route path="/live" element={<Dashboard />} />
            <Route path="/rehab" element={<RehabHome />} />
            <Route path="/progress" element={<ProgressScreen />} />
            <Route path="/alerts" element={<Alerts />} />
            <Route path="/settings" element={<Settings />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </HashRouter>
    </CloudProvider></AppProvider>
  )
}
