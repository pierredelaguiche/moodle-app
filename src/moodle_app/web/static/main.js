'use strict';
(function () { const b = $('#menubtn'); b.append(icon('menu')); const hb = $('#helpbtn'); hb.prepend(icon('help')); const m = $('#more'); m.prepend(icon('more')); const s = $('#sync'); s.prepend(icon('refresh')); $('#palbtn').prepend(icon('search')); boot(); })();
