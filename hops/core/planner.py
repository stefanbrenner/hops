
import io
import datetime
import numpy as np
import exoclock

from matplotlib.figure import Figure

from .common import ERRORS, HOPSError

HORIZON_DIRECTIONS = [['s', 0], ['sw', 45], ['w', 90], ['nw', 135], ['n', 180], ['ne', 225], ['e', 270], ['se', 315]]


def test_coordinates(ra_string, dec_string):
    try:
        exoclock.FixedTarget(exoclock.Hours(ra_string), exoclock.Degrees(dec_string))
        return True
    except ERRORS:
        return False


def test_date(year_month_string):
    parts = year_month_string.split()
    if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
        return False
    try:
        year, month = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return 1 <= month <= 12 and year >= 0


def defaults(log):
    now = datetime.datetime.now()
    values = {
        'observatory': log.get_param('observatory'),
        'latitude': log.get_param('observatory_lat'),
        'longitude': log.get_param('observatory_long'),
        'timezone': log.get_param('observatory_time_zone'),
        'year_month': '{0} {1}'.format(now.year, str(now.month).zfill(2)),
        'search': 'M42',
    }
    for name, _ in HORIZON_DIRECTIONS:
        values['horizon_{0}'.format(name)] = log.get_param('observatory_horizon_{0}'.format(name))
    return {key: '' if value is None else str(value) for key, value in values.items()}


def search(name):
    try:
        target = exoclock.simbad_search_by_name(name)
        if target:
            return {'name': target.name, 'ra': target.ra.hms(), 'dec': target.dec.dms_coord()}
    except ERRORS:
        pass
    return {'name': 'No results', 'ra': '', 'dec': ''}


def avc_plot(latitude, longitude, tmzn, horizon, target_ra, target_dec, year_mont_string, ax, name, observatory_name):
    """Visibility of a target during a month (port of ObservingPlannerWindow.avc_plot)."""

    ax.cla()

    target = exoclock.FixedTarget(exoclock.Hours(target_ra), exoclock.Degrees(target_dec))
    observatory = exoclock.Observatory(exoclock.Degrees(latitude), exoclock.Degrees(longitude), tmzn, horizon)

    year = int(year_mont_string.split()[0])
    month = int(year_mont_string.split()[1])

    months = ['xx', 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
              'October', 'November', 'December']
    if (year - 2000) / 4.0 - int((year - 2000) / 4.0) == 0:
        days = [0, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    else:
        days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

    jd_0 = exoclock.Moment('{0}-{1}-01 12:00:00'.format(year, str(month).zfill(2))).jd_utc() - tmzn / 24
    time_0 = exoclock.Moment(jd_utc=jd_0)

    events = []

    # mid-day splits
    for jj in range(days[month] + 1):
        events.append([exoclock.Moment(jd_utc=time_0.jd_utc() + jj), 'mid-day'])

    # target rise/set events
    events += observatory.target_horizon_crossings(target, time_0, days[month])

    # sun rise/set and -18 degrees events
    for jj in range(days[month]):

        check_time = exoclock.Moment(jd_utc=time_0.jd_utc() + jj)
        sun = exoclock.Sun(check_time)
        fixed_sun = exoclock.FixedTarget(sun.ra, sun.dec)

        sun_events = observatory.target_horizon_crossings(fixed_sun, check_time, 1)
        for i in range(len(sun_events)):
            if sun_events[i][1] == 'rise':
                sun_events[i][1] = 'sun_rise'
            elif sun_events[i][1] == 'set':
                sun_events[i][1] = 'sun_set'
        events += sun_events

        twilight_events = observatory.target_altitude_crossings(fixed_sun, check_time, 1, exoclock.Degrees(-18))
        for i in range(len(twilight_events)):
            if twilight_events[i][1] == 'rise':
                twilight_events[i][1] = 'sun_rise_18'
            elif twilight_events[i][1] == 'set':
                twilight_events[i][1] = 'sun_set_18'
        events += twilight_events

    events2 = [[ff[0].jd_utc(), ff[0], ff[1]] for ff in events]
    events2.sort(key=lambda ff: ff[0])

    ax.xaxis.tick_top()
    ax.set_title(observatory_name + '\n' + name + '   ' + months[month] + ' ' + str(year))
    ax.set_xlim((0, 1))
    ax.set_xlabel('HOUR (UTC{0:+.1f})'.format(tmzn))
    ax.set_xticks(np.arange(0, 24.5, 1))
    ax.set_xticklabels(('12', '13', '14', '15', '16', '17', '18', '19', '20', '21', '22', '23',
                        '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12'))
    ax.set_ylim((0, days[month] + 1))
    ax.set_ylabel('DAY')
    ax.set_yticks(np.arange(1, days[month] + 0.5, 1))
    ax.tick_params(bottom=True, top=True, left=True, right=True, labelbottom=True, labeltop=True,
                   labelright=False, labelleft=True)
    ax.grid(True, axis='y', linestyle='--')

    for jj, ii in enumerate(events2[:-1]):

        test_jd = 0.5 * (ii[0] + events2[jj + 1][0])
        check_time = exoclock.Moment(jd_utc=test_jd)

        day = 1 + int(test_jd - jd_0)

        time_range = [(ii[0] - jd_0 - int(ii[0] - jd_0)) * 24,
                      (events2[jj + 1][0] - jd_0 - int(events2[jj + 1][0] - jd_0)) * 24]
        if time_range[1] == 0:
            time_range[1] = 24

        alpha = 1
        if not observatory.is_target_visible(target, check_time):
            color = 'w'
            alpha = 0
        else:
            sun_az, sun_alt = observatory.target_azimuth_altitude(exoclock.Sun(check_time), check_time)
            if sun_alt.deg_coord() > 0:
                color = 'y'
            elif sun_alt.deg_coord() > -18:
                color = 'r'
            else:
                color = str(0.8 * exoclock.Moon(check_time).illumination())

        ax.plot(time_range, [day, day], linewidth=2.5, color=color, alpha=alpha)

        shift = {'left': +0.3, 'right': -0.3}

        if ii[2] == 'target_set':
            ax.plot(time_range[0], day, 'k*', mec='k', markersize=8)
            align = 'right' if time_range[0] > 20.5 else 'left'
            ax.text(time_range[0] + shift[align], day + 0.4,
                    'set: ' + (ii[1].utc + datetime.timedelta(days=tmzn / 24)).isoformat().split('T')[1][:5],
                    va='center', ha=align, fontsize=9)

        if ii[2] == 'target_rise':
            ax.plot(time_range[0], day, 'w*', mec='k', markersize=8, markeredgewidth=0.5)
            align = 'left' if time_range[0] < 3.5 else 'right'
            ax.text(time_range[0] + shift[align], day + 0.4,
                    'rise: ' + (ii[1].utc + datetime.timedelta(days=tmzn / 24)).isoformat().split('T')[1][:5],
                    va='center', ha=align, fontsize=9)


def plot(values, image_format='svg'):
    """Validates the planner form and returns the visibility chart as SVG text or PNG bytes."""

    if not test_coordinates(values['ra'], values['dec']):
        raise HOPSError('Wrong coordinates.')
    if not test_date(values['year_month']):
        raise HOPSError('Wrong date, use the format "yyyy mm".')
    try:
        timezone = float(values['timezone'])
        horizon = [[azimuth, float(values['horizon_{0}'.format(name)])] for name, azimuth in HORIZON_DIRECTIONS]
    except (TypeError, ValueError):
        raise HOPSError('Time zone and horizon altitudes must be numbers.')

    figure = Figure(figsize=(6, 6.5))
    figure.subplots_adjust(left=0.1, right=1 - 0.05, bottom=0.1, top=0.85)
    ax = figure.add_subplot(111)

    name = values.get('name') or values.get('search') or ''
    if name == 'No results':
        name = values.get('search', '')

    try:
        avc_plot(values['latitude'], values['longitude'], timezone, horizon, values['ra'], values['dec'],
                 values['year_month'], ax, name, values.get('observatory', ''))
    except ERRORS as e:
        raise HOPSError('The plot could not be created, please check the observatory coordinates ({0}).'.format(e))

    buffer = io.BytesIO()
    figure.savefig(buffer, format=image_format, dpi=200 if image_format == 'png' else None)
    return buffer.getvalue()
