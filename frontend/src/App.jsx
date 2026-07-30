import { Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell.jsx";
import HomePage from "./pages/HomePage.jsx";
import BookDetailPage from "./pages/BookDetailPage.jsx";
import VoicesPage from "./pages/VoicesPage.jsx";
import PronunciationsPage from "./pages/PronunciationsPage.jsx";
import TextPage from "./pages/TextPage.jsx";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/books/:bookId" element={<BookDetailPage />} />
        <Route path="/voices" element={<VoicesPage />} />
        <Route path="/pronunciations" element={<PronunciationsPage />} />
        <Route path="/text" element={<TextPage />} />
      </Route>
    </Routes>
  );
}
