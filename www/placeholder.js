/* Фотографии товаров в репозиторий не включены.
   Пока файла нет, на его месте рисуется заглушка: страница остаётся целой,
   вёрстку и конфигуратор можно смотреть без единого снимка.
   Свои фотографии кладите рядом в www/ под теми же именами. */
(function () {
  'use strict';

  var drawn = 'data-placeholder';

  function label(img) {
    var src = img.getAttribute('src') || '';
    var name = src.split('/').pop().replace(/\.[a-z0-9]+$/i, '');
    return name || 'фото';
  }

  function placeholder(img) {
    var w = img.naturalWidth || img.width || 800;
    var h = img.naturalHeight || img.height || 600;
    if (!h || h < 40) { w = 800; h = 600; }

    var svg =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + w + ' ' + h + '">' +
        '<rect width="100%" height="100%" fill="#e8e3dc"/>' +
        '<g fill="none" stroke="#c3b9ab" stroke-width="2">' +
          '<rect x="1" y="1" width="' + (w - 2) + '" height="' + (h - 2) + '"/>' +
        '</g>' +
        '<text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle" ' +
              'font-family="Georgia, serif" font-size="' + Math.max(13, Math.round(Math.min(w, h) / 16)) + '" ' +
              'fill="#8d8377">' + label(img).replace(/[<>&]/g, '') + '</text>' +
      '</svg>';

    return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
  }

  /* Событие error у <img> не всплывает — слушаем на фазе перехвата,
     иначе картинки, добавленные скриптом позже, останутся битыми. */
  document.addEventListener('error', function (e) {
    var img = e.target;
    if (!img || img.tagName !== 'IMG' || img.hasAttribute(drawn)) return;
    img.setAttribute(drawn, '');
    img.src = placeholder(img);
  }, true);
})();
