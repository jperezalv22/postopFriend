// Selector de tema visual.
//
// Lo único que hace es reescribir `data-tema` en <html>. Todo el color del
// proyecto cuelga de ese atributo (ver el bloque 1 de app.css), así que no hay
// una sola clase de tema repartida por los componentes y cambiar de piel no
// puede desincronizar nada: no existe un estado intermedio en el que la mitad de
// la pantalla se haya enterado y la otra mitad no.
//
// El tema guardado se aplica ANTES de pintar, con el trozo en línea que hay en el
// <head> de cada página. Este archivo llega después y solo pone los botones de
// acuerdo con lo que ya quedó puesto: si aplicara el tema desde aquí, la primera
// pintada saldría con el tema por defecto y la segunda con el guardado — un
// parpadeo de piel entera en cada navegación entre las cuatro pantallas.

(function () {
  const CLAVE = "postopfriend-tema";
  const TEMAS = ["monitor", "clinico"];

  const botones = document.querySelectorAll("[data-set-tema]");
  if (!botones.length) return;

  function fijar(tema) {
    document.documentElement.dataset.tema = tema;
    botones.forEach((b) => {
      const activo = b.dataset.setTema === tema;
      b.classList.toggle("activo", activo);
      b.setAttribute("aria-pressed", activo ? "true" : "false");
    });
    // Modo privado o almacenamiento bloqueado: el tema vale para esta pestaña y
    // no se guarda. Es degradación aceptable; reventar aquí no lo sería.
    try { localStorage.setItem(CLAVE, tema); } catch (e) { /* sin persistencia */ }
  }

  botones.forEach((b) =>
    b.addEventListener("click", () => fijar(b.dataset.setTema)));

  const puesto = document.documentElement.dataset.tema;
  fijar(TEMAS.includes(puesto) ? puesto : TEMAS[0]);
})();
