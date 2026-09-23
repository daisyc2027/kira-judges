(function() {
  var API_URL = localStorage.getItem('kira_api_url') || 'http://127.0.0.1:5000';
  var screenPaths = {
    onboarding: 'onboarding.html',
    home: 'home.html',
    chat: 'chat.html',
    cycle: 'cycle.html',
    diet: 'diet.html',
    sleep: 'sleep.html',
    insights: 'insights.html',
    data: 'data.html',
    interventions: 'interventions.html',
    settings: 'settings.html',
    breathing: 'Activities/breathing.html',
    grounding: 'Activities/grounding.html',
    haptic: 'Activities/haptic.html',
    journal: 'Activities/journal.html',
    urge_surf: 'Activities/urge_surf.html',
    logout: 'onboarding.html'
  };

  function fromActivitiesPath(path) {
    return path.indexOf('/Activities/') !== -1 || path.indexOf('Activities/') === 0;
  }

  function resolveScreenPath(screen) {
    var path = screenPaths[screen] || screenPaths.home;
    if (fromActivitiesPath(window.location.pathname) && path.indexOf('Activities/') !== 0) {
      return '../' + path;
    }
    if (!fromActivitiesPath(window.location.pathname) && path.indexOf('Activities/') === 0) {
      return path;
    }
    return path.replace('Activities/', '');
  }

  function getUserId() {
    return localStorage.getItem('kira_user') || 'user_1';
  }

  function setUserId(userId) {
    if (userId) localStorage.setItem('kira_user', userId);
  }

  function getAuthToken() {
    return localStorage.getItem('kira_auth_token') || '';
  }

  function setAuthToken(token) {
    if (token) localStorage.setItem('kira_auth_token', token);
  }

  var NOTIFICATION_PREFS_KEY = 'kira_notif_prefs';
  var OLD_NOTIFICATION_PREFS_KEY = 'kira_notification_prefs';
  var DEFAULT_NOTIFICATION_PREFS = {
    checkin: false,
    sleep: false,
    water: false,
    period: false
  };

  function normalizeNotificationPrefs(prefs) {
    prefs = prefs || {};
    return {
      checkin: !!(prefs.checkin || prefs.morning),
      sleep: !!prefs.sleep,
      water: !!prefs.water,
      period: !!prefs.period
    };
  }

  function getNotificationPrefs() {
    var raw = localStorage.getItem(NOTIFICATION_PREFS_KEY) || localStorage.getItem(OLD_NOTIFICATION_PREFS_KEY);
    if (!raw) return Object.assign({}, DEFAULT_NOTIFICATION_PREFS);
    try {
      return normalizeNotificationPrefs(JSON.parse(raw));
    } catch (e) {
      return Object.assign({}, DEFAULT_NOTIFICATION_PREFS);
    }
  }

  function setNotificationPrefs(prefs) {
    var normalized = normalizeNotificationPrefs(prefs);
    localStorage.setItem(NOTIFICATION_PREFS_KEY, JSON.stringify(normalized));
    localStorage.removeItem(OLD_NOTIFICATION_PREFS_KEY);
    return normalized;
  }

  function installAuthFetch() {
    if (!window.fetch || window.KiraFetchWrapped) return;
    var originalFetch = window.fetch.bind(window);
    window.fetch = function(input, options) {
      var url = typeof input === 'string' ? input : (input && input.url) || '';
      var token = getAuthToken();
      if (!token || url.indexOf(API_URL) !== 0) {
        return originalFetch(input, options);
      }

      var nextOptions = options ? Object.assign({}, options) : {};
      var headers = new Headers(nextOptions.headers || (input && input.headers) || {});
      if (!headers.has('Authorization')) {
        headers.set('Authorization', 'Bearer ' + token);
      }
      nextOptions.headers = headers;
      return originalFetch(input, nextOptions);
    };
    window.KiraFetchWrapped = true;
  }

  function navigate(screen, userId) {
    if (screen === 'logout') {
      localStorage.removeItem('kira_user');
      localStorage.removeItem('kira_auth_token');
      localStorage.removeItem('kira_return_to');
    } else {
      setUserId(userId);
    }
    window.location.href = resolveScreenPath(screen);
  }

  async function vibrate(pattern) {
    try {
      if (pattern === 'stop') {
        if (navigator.vibrate) navigator.vibrate(0);
        return;
      }
      if (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.Haptics) {
        var Haptics = window.Capacitor.Plugins.Haptics;
        if (typeof Haptics.vibrate === 'function') {
          await Haptics.vibrate({ duration: pattern === 'calm' ? 700 : 260 });
          return;
        }
        var sequence = pattern === 'energy'
          ? [{ style: 'Medium', wait: 0 }, { style: 'Light', wait: 80 }, { style: 'Medium', wait: 80 }, { style: 'Heavy', wait: 90 }, { style: 'Light', wait: 90 }]
          : [{ style: 'Heavy', wait: 0 }, { style: 'Medium', wait: 110 }, { style: 'Light', wait: 110 }, { style: 'Medium', wait: 240 }];
        for (var i = 0; i < sequence.length; i++) {
          if (sequence[i].wait) {
            await new Promise(function(resolve) { setTimeout(resolve, sequence[i].wait); });
          }
          await Haptics.impact({ style: sequence[i].style });
        }
      } else if (navigator.vibrate) {
        navigator.vibrate(pattern === 'calm' ? [420, 220, 420, 220, 420] : [140, 80, 140, 80, 220, 80, 140]);
      }
    } catch (e) {}
  }

  function localNotificationsPlugin() {
    return window.Capacitor &&
      window.Capacitor.Plugins &&
      window.Capacitor.Plugins.LocalNotifications;
  }

  async function ensureNotificationPermission() {
    var LocalNotifications = localNotificationsPlugin();
    if (!LocalNotifications) {
      return { granted: false, reason: 'Device notifications require the iOS or Android app build.' };
    }

    var shouldRequest = !arguments[0] || arguments[0].requestIfPrompt !== false;
    var status = { display: 'prompt' };
    if (typeof LocalNotifications.checkPermissions === 'function') {
      status = await LocalNotifications.checkPermissions();
    }
    if (status.display === 'denied') {
      return { granted: false, denied: true, reason: 'Notifications are blocked. Open device Settings, then Kira, then Notifications to allow them.' };
    }
    if (status.display !== 'granted' && shouldRequest && typeof LocalNotifications.requestPermissions === 'function') {
      status = await LocalNotifications.requestPermissions();
    }
    if (status.display === 'denied') {
      return { granted: false, denied: true, reason: 'Notifications are blocked. Open device Settings, then Kira, then Notifications to allow them.' };
    }
    if (status.display !== 'granted') {
      return { granted: false, reason: 'Notifications are not allowed yet. Please allow them in device settings.' };
    }

    if (
      window.Capacitor &&
      typeof window.Capacitor.getPlatform === 'function' &&
      window.Capacitor.getPlatform() === 'android' &&
      typeof LocalNotifications.createChannel === 'function'
    ) {
      try {
        await LocalNotifications.createChannel({
          id: 'kira-wellness',
          name: 'Kira wellness reminders',
          description: 'Daily check-in, sleep, water, and period reminders',
          importance: 4,
          visibility: 1,
          sound: 'default'
        });
      } catch (e) {}
    }

    return { granted: true };
  }

  var REMINDER_NOTIFICATION_IDS = {
    checkin: [1101, 1102, 1103, 1104, 1105, 1106, 1107],
    sleep: [1201, 1202, 1203, 1204, 1205, 1206, 1207],
    water: [1301],
    period: [1401, 1402, 1403, 1404, 1405]
  };

  function allReminderNotificationIds() {
    return REMINDER_NOTIFICATION_IDS.checkin
      .concat(REMINDER_NOTIFICATION_IDS.sleep)
      .concat(REMINDER_NOTIFICATION_IDS.water)
      .concat(REMINDER_NOTIFICATION_IDS.period);
  }

  async function cancelScheduledReminders() {
    var LocalNotifications = localNotificationsPlugin();
    if (!LocalNotifications || typeof LocalNotifications.cancel !== 'function') return;
    await LocalNotifications.cancel({
      notifications: allReminderNotificationIds().map(function(id) { return { id: id }; })
    });
  }

  async function turnOffNotificationPrefs() {
    var prefs = setNotificationPrefs(DEFAULT_NOTIFICATION_PREFS);
    await cancelScheduledReminders();
    return prefs;
  }

  function minutesFromTime(value) {
    if (!value || typeof value !== 'string' || value.indexOf(':') === -1) return null;
    var parts = value.split(':');
    var hour = parseInt(parts[0], 10);
    var minute = parseInt(parts[1], 10);
    if (isNaN(hour) || isNaN(minute) || hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
    return hour * 60 + minute;
  }

  function clampMinutes(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function averageMinutes(values) {
    if (!values.length) return null;
    var total = values.reduce(function(sum, value) { return sum + value; }, 0);
    return Math.round(total / values.length);
  }

  function timeFromMinutes(value) {
    value = ((Math.round(value) % 1440) + 1440) % 1440;
    return { hour: Math.floor(value / 60), minute: value % 60 };
  }

  function parseLogDate(log) {
    var raw = log && log.date;
    if (!raw) return null;
    var date = new Date(String(raw).replace(' ', 'T'));
    return isNaN(date.getTime()) ? null : date;
  }

  function weekdayFromDate(date) {
    return date.getDay() + 1;
  }

  function previousWeekday(weekday) {
    return weekday === 1 ? 7 : weekday - 1;
  }

  function sleepPatternsFromLogs(logs) {
    var wakeByWeekday = {};
    var bedtimeByWeekday = {};
    var allWake = [];
    var allBedtime = [];

    (logs || []).forEach(function(log) {
      var date = parseLogDate(log);
      if (!date) return;

      var wake = minutesFromTime(log.wake_time);
      if (wake !== null) {
        var wakeWeekday = weekdayFromDate(date);
        wakeByWeekday[wakeWeekday] = wakeByWeekday[wakeWeekday] || [];
        wakeByWeekday[wakeWeekday].push(wake);
        allWake.push(wake);
      }

      var bedtime = minutesFromTime(log.bedtime);
      if (bedtime !== null) {
        var bedWeekday = weekdayFromDate(date);
        if (wake !== null && bedtime > wake) {
          bedWeekday = previousWeekday(bedWeekday);
        }
        var adjustedBedtime = bedtime < 12 * 60 ? bedtime + 24 * 60 : bedtime;
        bedtimeByWeekday[bedWeekday] = bedtimeByWeekday[bedWeekday] || [];
        bedtimeByWeekday[bedWeekday].push(adjustedBedtime);
        allBedtime.push(adjustedBedtime);
      }
    });

    return {
      wakeByWeekday: wakeByWeekday,
      bedtimeByWeekday: bedtimeByWeekday,
      allWake: allWake,
      allBedtime: allBedtime
    };
  }

  function bestWeekdayTime(patterns, weekday, field, fallback) {
    var byWeekday = field === 'wake' ? patterns.wakeByWeekday : patterns.bedtimeByWeekday;
    var all = field === 'wake' ? patterns.allWake : patterns.allBedtime;
    var values = byWeekday[weekday] && byWeekday[weekday].length >= 2 ? byWeekday[weekday] : all;
    return averageMinutes(values) || fallback;
  }

  async function fetchJson(url) {
    var response = await fetch(url);
    if (!response.ok) throw new Error('Request failed');
    return response.json();
  }

  async function fetchSleepLogsForNotifications() {
    try {
      var userId = encodeURIComponent(getUserId());
      var data = await fetchJson(API_URL + '/recent_logs?type=sleep&user_id=' + userId + '&limit=30');
      return data.logs || [];
    } catch (e) {
      return [];
    }
  }

  async function fetchCycleForNotifications() {
    try {
      var userId = encodeURIComponent(getUserId());
      return await fetchJson(API_URL + '/cycle_status?user_id=' + userId);
    } catch (e) {
      return null;
    }
  }

  function buildWeeklySleepNotifications(patterns, prefs) {
    var notifications = [];
    for (var weekday = 1; weekday <= 7; weekday++) {
      if (prefs.checkin) {
        var wakeTime = bestWeekdayTime(patterns, weekday, 'wake', 8 * 60 + 30);
        var checkinTime = timeFromMinutes(clampMinutes(wakeTime + 30, 7 * 60, 11 * 60 + 30));
        notifications.push({
          id: REMINDER_NOTIFICATION_IDS.checkin[weekday - 1],
          title: 'Good morning',
          body: 'Take a soft moment to check in with yourself.',
          schedule: { on: { weekday: weekday, hour: checkinTime.hour, minute: checkinTime.minute }, repeats: true },
          channelId: 'kira-wellness',
          extra: { type: 'checkin', action: 'navigate:home' }
        });
      }

      if (prefs.sleep) {
        var bedtime = bestWeekdayTime(patterns, weekday, 'bedtime', 22 * 60 + 30);
        var sleepTime = timeFromMinutes(clampMinutes(bedtime - 45, 20 * 60, 23 * 60 + 30));
        notifications.push({
          id: REMINDER_NOTIFICATION_IDS.sleep[weekday - 1],
          title: 'Wind down',
          body: 'Your usual rest window is coming up. Ready to settle in?',
          schedule: { on: { weekday: weekday, hour: sleepTime.hour, minute: sleepTime.minute }, repeats: true },
          channelId: 'kira-wellness',
          extra: { type: 'sleep', action: 'navigate:sleep' }
        });
      }
    }
    return notifications;
  }

  function buildWaterNotification(patterns, prefs) {
    if (!prefs.water) return [];
    var averageWake = averageMinutes(patterns.allWake) || 8 * 60 + 30;
    var waterTime = timeFromMinutes(clampMinutes(averageWake + 4 * 60, 11 * 60 + 30, 14 * 60));
    return [{
      id: REMINDER_NOTIFICATION_IDS.water[0],
      title: 'Water check',
      body: 'A small hydration pause, if it feels useful.',
      schedule: { on: { hour: waterTime.hour, minute: waterTime.minute }, repeats: true },
      channelId: 'kira-wellness',
      extra: { type: 'water', action: 'navigate:diet' }
    }];
  }

  function buildPeriodNotifications(cycle, prefs) {
    if (!prefs.period || !cycle || !cycle.predicted_period_start_date) return [];
    var start = new Date(cycle.predicted_period_start_date + 'T10:00:00');
    if (isNaN(start.getTime())) return [];

    var reminders = [
      { offset: -2, body: 'Your period is predicted in about 2 days. You can update Cycle if things feel different.' },
      { offset: -1, body: 'Your period may be close. Kira will wait for your confirmation.' },
      { offset: 0, body: 'Your period is predicted today. Open Cycle to confirm when it arrives.' }
    ];
    var now = new Date();
    var notifications = [];
    reminders.forEach(function(reminder, index) {
      var at = new Date(start.getTime());
      at.setDate(at.getDate() + reminder.offset);
      if (at <= now) return;
      notifications.push({
        id: REMINDER_NOTIFICATION_IDS.period[index],
        title: 'Cycle reminder',
        body: reminder.body,
        schedule: { at: at },
        channelId: 'kira-wellness',
        extra: { type: 'period', action: 'navigate:cycle' }
      });
    });
    return notifications;
  }

  async function refreshScheduledNotifications(options) {
    options = options || {};
    var prefs = normalizeNotificationPrefs(options.prefs || getNotificationPrefs());
    await cancelScheduledReminders();

    if (!prefs.checkin && !prefs.sleep && !prefs.water && !prefs.period) {
      return { granted: true, scheduled: false, count: 0 };
    }

    var permission = await ensureNotificationPermission({ requestIfPrompt: options.requestPermission === true });
    if (!permission.granted) return permission;

    var sleepLogs = await fetchSleepLogsForNotifications();
    var cycle = prefs.period ? await fetchCycleForNotifications() : null;
    var patterns = sleepPatternsFromLogs(sleepLogs);
    var notifications = []
      .concat(buildWeeklySleepNotifications(patterns, prefs))
      .concat(buildWaterNotification(patterns, prefs))
      .concat(buildPeriodNotifications(cycle, prefs));

    if (notifications.length) {
      await localNotificationsPlugin().schedule({ notifications: notifications });
    }
    localStorage.setItem('kira_last_notification_schedule', JSON.stringify({
      updatedAt: new Date().toISOString(),
      count: notifications.length,
      personalisedSleepLogs: patterns.allWake.length + patterns.allBedtime.length,
      prefs: prefs
    }));
    return { granted: true, scheduled: true, count: notifications.length };
  }

  function installNotificationTapHandler() {
    var LocalNotifications = localNotificationsPlugin();
    if (!LocalNotifications || window.KiraNotificationTapHandlerInstalled) return;
    window.KiraNotificationTapHandlerInstalled = true;
    LocalNotifications.addListener('localNotificationActionPerformed', function(event) {
      var extra = event && event.notification && event.notification.extra;
      var action = extra && extra.action;
      if (typeof action === 'string' && action.indexOf('navigate:') === 0) {
        handleBridgeValue(action);
      }
    });
  }

  function handleBridgeValue(value) {
    if (!value || typeof value !== 'string') return;
    var parts = value.split(':');
    var type = parts[0];
    if (type === 'navigate') {
      navigate(parts[1] || 'home', parts[2]);
    } else if (type === 'vibrate') {
      vibrate(parts[1] || 'default');
    } else if (type === 'notif_prefs') {
      try {
        setNotificationPrefs(JSON.parse(parts.slice(1).join(':')));
      } catch (e) {}
    }
  }

  function updateKeyboardInset() {
    var inset = 0;
    if (window.visualViewport) {
      inset = Math.max(0, window.innerHeight - window.visualViewport.height - window.visualViewport.offsetTop);
    }
    document.documentElement.style.setProperty('--kira-keyboard-inset', Math.round(inset) + 'px');
    document.documentElement.classList.toggle('keyboard-open', inset > 40);
  }

  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', updateKeyboardInset);
    window.visualViewport.addEventListener('scroll', updateKeyboardInset);
  }

  window.Kira = {
    API_URL: API_URL,
    getUserId: getUserId,
    setUserId: setUserId,
    getAuthToken: getAuthToken,
    setAuthToken: setAuthToken,
    getNotificationPrefs: getNotificationPrefs,
    setNotificationPrefs: setNotificationPrefs,
    ensureNotificationPermission: ensureNotificationPermission,
    refreshScheduledNotifications: refreshScheduledNotifications,
    turnOffNotificationPrefs: turnOffNotificationPrefs,
    navigate: navigate,
    vibrate: vibrate,
    updateKeyboardInset: updateKeyboardInset,
    handleBridgeValue: handleBridgeValue
  };

  installAuthFetch();

  if (!window.AppInventor) {
    window.AppInventor = {
      getWebViewString: function() {
        return 'user:' + getUserId();
      },
      setWebViewString: handleBridgeValue
    };
  }

  document.addEventListener('DOMContentLoaded', function() {
    document.documentElement.classList.add('capacitor-ready');
    document.documentElement.style.setProperty('--kira-keyboard-inset', '0px');
    updateKeyboardInset();
    installNotificationTapHandler();
    refreshScheduledNotifications({ requestPermission: false }).catch(function() {});
  });
})();
