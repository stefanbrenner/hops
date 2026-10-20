
import datetime
import exoclock

from hops.application_windows import MainWindow
from hops.core import planner


def test_coordinates2(ra_string, dec_string):
    if planner.test_coordinates(ra_string, dec_string):
        return [True, 'Coordinates\naccepted']
    return [False, 'Wrong\ncoordinates']


def test_date(year_month_string):
    if planner.test_date(year_month_string):
        return [True, 'Date\naccepted']
    return [False, 'Wrong\ndate']


class ObservingPlannerWindow(MainWindow):

    def __init__(self, log):

        MainWindow.__init__(self, log, name='HOPS - Observing Planner', position=2)

        # get variables from log and set as tk variables those to be modified

        self.target = self.Label(text=' ', instance=str)
        self.target_search = self.Entry(value='M42', instance=str)

        self.target_ra = self.Entry(value=' ', instance=str, command=self.update_window)
        self.target_dec = self.Entry(value=' ', instance=str, command=self.update_window)
        self.target_ra_dec_test = self.Label(text=' ')

        now = datetime.datetime.now()
        self.obs_year_month = self.Entry(value='{0} {1}'.format(now.year, str(now.month).zfill(2)), instance=str,
                                         command=self.update_window)
        self.obs_year_month_test = self.Label(text=' ')

        self.latitude = self.Entry(value=self.log.get_param('observatory_lat'), instance=str)
        self.longitude = self.Entry(value=self.log.get_param('observatory_long'), instance=str)
        self.horizon_s = self.Entry(value=self.log.get_param('observatory_horizon_s'), instance=float)
        self.horizon_sw = self.Entry(value=self.log.get_param('observatory_horizon_sw'), instance=float)
        self.horizon_w = self.Entry(value=self.log.get_param('observatory_horizon_w'), instance=float)
        self.horizon_nw = self.Entry(value=self.log.get_param('observatory_horizon_nw'), instance=float)
        self.horizon_n = self.Entry(value=self.log.get_param('observatory_horizon_n'), instance=float)
        self.horizon_ne = self.Entry(value=self.log.get_param('observatory_horizon_ne'), instance=float)
        self.horizon_e = self.Entry(value=self.log.get_param('observatory_horizon_e'), instance=float)
        self.horizon_se = self.Entry(value=self.log.get_param('observatory_horizon_se'), instance=float)
        self.observatory = self.Entry(value=self.log.get_param('observatory'), instance=str)
        self.timezone = self.Entry(value=self.log.get_param('observatory_time_zone'), instance=float)

        # create widgets

        self.figure = self.FigureWindow(figsize=(6, 6.5), show_nav=True)
        self.ax1 = self.figure.figure.add_subplot(111)
        self.figure.figure.subplots_adjust(left=0.1, right=1 - 0.05, bottom=0.1, top=0.85)

        self.plot_button = self.Button(text='RESET PLOT', command=self.plot)

        # setup window

        self.setup_window([
            [[self.figure, 4, 1, 30]],
            [[self.Label(text='Observatory'), 2]],
            [[self.observatory, 2]],
            [],
            [[self.Label(text='Latitude'), 1],
             [self.Label(text='Horizon altitude S (deg)'), 2],
             [self.horizon_s, 3]],
            [[self.latitude, 1],
             [self.Label(text='Horizon altitude SW (deg)'), 2],
             [self.horizon_sw, 3]],
            [[self.Label(text='Horizon altitude W (deg)'), 2],
             [self.horizon_w, 3]],
            [[self.Label(text='Longitude'), 1],
             [self.Label(text='Horizon altitude NW (deg)'), 2],
             [self.horizon_nw, 3]],
            [[self.longitude, 1],
             [self.Label(text='Horizon altitude N (deg)'), 2],
             [self.horizon_n, 3]],
            [[self.Label(text='Horizon altitude NE (deg)'), 2],
             [self.horizon_ne,3]],
            [[self.Label(text='Time Zone'), 1],
             [self.Label(text='Horizon altitude E (deg)'), 2],
             [self.horizon_e, 3]],
            [[self.timezone, 1],
             [self.Label(text='Horizon altitude SE (deg)'), 2],
             [self.horizon_se, 3]],
            [],
            [[self.Label(text='Object'), 1]],
            [[self.target_search, 1],
             [self.Button(text='SEARCH RA/DEC', command=self.search_object), 2], [self.target, 3]],
            [],
            [[self.Label(text='Manual target RA DEC\n(hh:mm:ss +/-dd:mm:ss)'), 1, 1, 2], [self.target_ra, 2],
             [self.target_ra_dec_test, 3, 1, 2]],
            [[self.target_dec, 2]],
            [[self.Label(text='Observation year and month\n(yyyy mm)'), 1], [self.obs_year_month, 2],
             [self.obs_year_month_test, 3]],
            [],
            [[self.plot_button, 2]],
            [],
        ])

        self.search_object()

    def update_window(self, *event):

        if not event:
            pass

        check_ra_dec = test_coordinates2(self.target_ra.get(), self.target_dec.get())
        self.target_ra_dec_test.set(check_ra_dec[1])
        check_year_month = test_date(self.obs_year_month.get())
        self.obs_year_month_test.set(check_year_month[1])

        if check_ra_dec[0] and check_year_month[0]:
            self.plot_button.activate()
        else:
            self.plot_button.disable()

    def search_object(self):

        try:
            target = exoclock.simbad_search_by_name(self.target_search.get())

            if target:
                self.target.set(target.name)
                self.target_ra.set(target.ra.hms())
                self.target_dec.set(target.dec.dms_coord())
            else:
                self.target_ra.set('')
                self.target_dec.set('')
                self.target.set('No results')

        except:
            self.target_ra.set('')
            self.target_dec.set('')
            self.target.set('No results')

        self.plot()
        self.update_window()

    def plot(self):
        self.ax1.cla()

        try:

            name = self.target.get()
            if name == 'No results':
                name = self.target_search.get()

            self.avc_plot(self.latitude.get(), self.longitude.get(), self.timezone.get(),
                          [
                              [0, self.horizon_s.get()],
                              [45, self.horizon_sw.get()],
                              [90, self.horizon_w.get()],
                              [135, self.horizon_nw.get()],
                              [180, self.horizon_n.get()],
                              [225, self.horizon_ne.get()],
                              [270, self.horizon_e.get()],
                              [315, self.horizon_se.get()],

                          ],
                          self.target_ra.get(), self.target_dec.get(), self.obs_year_month.get(), self.ax1,
                          name, self.observatory.get())

        except:
            pass

        self.figure.draw()

    def avc_plot(self, latitude, longitude, tmzn, horizon, target_ra, target_dec, year_mont_string, ax, name,
                 observatory_name):
        planner.avc_plot(latitude, longitude, tmzn, horizon, target_ra, target_dec, year_mont_string, ax, name,
                         observatory_name)
