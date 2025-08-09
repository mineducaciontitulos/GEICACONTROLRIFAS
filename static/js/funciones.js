let seleccionados = [];

function seleccionarNumero(element) {
    const numero = element.textContent;

    if (seleccionados.includes(numero)) {
        seleccionados = seleccionados.filter(n => n !== numero);
        element.classList.remove('seleccionado');
    } else {
        seleccionados.push(numero);
        element.classList.add('seleccionado');
    }

    document.getElementById("contador").textContent = seleccionados.length;
    document.getElementById("numeros_seleccionados").value = seleccionados.join(",");
}

function resetearSeleccion() {
    document.querySelectorAll(".numero").forEach(el => el.classList.remove('seleccionado'));
    seleccionados = [];
    document.getElementById("contador").textContent = 0;
    document.getElementById("numeros_seleccionados").value = "";
}