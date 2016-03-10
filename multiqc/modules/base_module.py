#!/usr/bin/env python

""" MultiQC modules base class, contains helper functions """

from __future__ import print_function
import base64
from collections import OrderedDict
import fnmatch
import io
import json
import logging
import mimetypes
import os
import random

# Import matplot lib but avoid default X environment
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from multiqc.utils import report, config
logger = logging.getLogger(__name__)

letters = 'abcdefghijklmnopqrstuvwxyz'

class BaseMultiqcModule(object):

    def __init__(self, name='base', anchor='base', target='',href='', info='', extra=''):
        self.name = name
        self.anchor = anchor
        if not target:
            target = self.name
        self.intro = '<p><a href="{0}" target="_blank">{1}</a> {2}</p>{3}'.format(
            href, target, info, extra
        )

    def find_log_files(self, patterns, filecontents=True, filehandles=False):
        """
        Search the analysis directory for log files of interest. Can take either a filename
        suffix or a search string to return only log files that contain relevant info.
        :param patterns: Dict with keys 'fn' or 'contents' (or both). Keys can contain
        string or a list of strings. 'fn' matches filenames, 'contents' matches file contents.
        NB: Both searches return file if *any* of the supplied strings are matched.
        :param filehandles: Set to true to return a file handle instead of slurped file contents
        :return: Yields a set with two items - a sample name generated from the filename
                 and either the file contents or file handle for the current matched file.
                 As yield is used, the function can be iterated over without 
        """
        
        # Get the search parameters
        fn_match = None
        contents_match = None
        if 'fn' in patterns:
            fn_match = patterns['fn']
        if 'contents' in patterns:
            contents_match = patterns['contents']
        if fn_match == None and contents_match == None:
            logger.warning("No file patterns specified for find_log_files")
            yield None
        
        # Step 1 - make a list of files to search
        files = list()
        for directory in config.analysis_dir:
            if os.path.isdir(directory):
                for root, dirnames, filenames in os.walk(directory, followlinks=True):
                    for fn in filenames:
                        files.append({
                            'root': root,
                            'fn': fn
                        })
            elif os.path.isfile(directory):
                files.append({
                    'root': os.path.dirname(directory),
                    'fn': os.path.basename(directory)
                })
                
        # Step 2 - loop through files, yield results if we find something
        for f in files:
            
            # Set up vars
            root = f['root']
            fn = f['fn']
            
            # Ignore files set in config
            i_matches = [n for n in config.fn_ignore_files if fnmatch.fnmatch(fn, n)]
            if len(i_matches) > 0:
                continue
            
            # Make a sample name from the filename
            s_name = self.clean_s_name(fn, root)
            
            # Make search strings into lists if a string is given
            if type(fn_match) is str:
                fn_match = [fn_match]
            if type(contents_match) is str:
                contents_match = [contents_match]
            
            # Search for file names ending in a certain string
            readfile = False
            fn_matched = False
            if fn_match is not None:
                for m in fn_match:
                    if m in fn:
                        readfile = True
                        fn_matched = True
                        break
            
            if contents_match is not None and readfile is False:
                
                # Use mimetypes to exclude binary files where possible
                (ftype, encoding) = mimetypes.guess_type(os.path.join(root, fn))
                if encoding is not None:
                    continue
                if ftype is not None and ftype.startswith('text') is False:
                    continue
                
                # Limit search to files under 1MB to avoid 30GB FastQ files etc.
                try:
                    filesize = os.path.getsize(os.path.join(root,fn))
                except (IOError, OSError, ValueError, UnicodeDecodeError):
                    logger.debug("Couldn't read file when checking filesize: {}".format(fn))
                else:
                    if filesize > config.log_filesize_limit:
                        logger.debug("Ignoring file as too large: {}".format(fn))
                    else:
                        readfile = True
            
            if readfile:
                try:
                    with io.open (os.path.join(root,fn), "r", encoding='utf-8') as f:
                        
                        # Search this file for our string of interest
                        returnfile = False
                        if contents_match is not None and fn_matched is False:
                            for line in f:
                                for m in contents_match:
                                    if m in line:
                                        returnfile = True
                                        break
                            f.seek(0)
                        else:
                            returnfile = True
                        
                        # Give back what was asked for. Yield instead of return
                        # so that this function can be used as an interator
                        # without loading all files at once.
                        if returnfile:
                            if filehandles:
                                yield {'s_name': s_name, 'f': f, 'root': root, 'fn': fn}
                            elif filecontents:
                                yield {'s_name': s_name, 'f': f.read(), 'root': root, 'fn': fn}
                            else:
                                yield {'s_name': s_name, 'root': root, 'fn': fn}
                except (IOError, OSError, ValueError, UnicodeDecodeError):
                    logger.debug("Couldn't read file when looking for output: {}".format(fn))
    
    
    def clean_s_name(self, s_name, root):
        """ Helper function to take a long file name and strip it
        back to a clean sample name. Somewhat arbitrary.
        :param s_name: The sample name to clean
        :param root: The directory path that this file is within
        :param prepend_dirs: boolean, whether to prepend dir name to s_name
        :param trimmed: boolean, remove common trimming suffixes from name?
        :return: The cleaned sample name, ready to be used
        """
        if root is None:
            root = ''
        # Split then take first section to remove everything after these matches
        for ext in config.fn_clean_exts:
            s_name = s_name.split(ext ,1)[0]
        if config.prepend_dirs:
            s_name = "{} | {}".format(root.replace(os.sep, ' | '), s_name).lstrip('. | ')
        return s_name
    
    
    def general_stats_addcols(self, data, headers={}, namespace=None):
        """ Helper function to add to the General Statistics variable.
        Adds to report.general_stats and does not return anything. Fills
        in required config variables if not supplied.
        :param data: A dict with the data. First key should be sample name,
                     then the data key, then the data.
        :param headers: Dict / OrderedDict with information for the headers, 
                        such as colour scales, min and max values etc.
                        See docs/writing_python.md for more information.
        :return: None
        """
        if namespace is None:
            namespace = self.name
        
        keys = data.keys()
        if len(headers.keys()) > 0:
            keys = headers.keys()
        for k in keys:
            # Unique id to avoid overwriting by other modules
            if self.name is None:
                headers[k]['rid'] = '{}_{}'.format(''.join(random.sample(letters, 4)), k)
            else:
                safe_name = ''.join(c for c in self.name if c.isalnum()).lower()
                headers[k]['rid'] = '{}_{}'.format(safe_name, k)
            
            # Use defaults / data keys if headers not given
            if 'title' not in headers[k]:
                headers[k]['title'] = k
            
            if 'description' not in headers[k]:
                headers[k]['description'] = '{}: {}'.format(self.name, headers[k]['title'])

            if 'scale' not in headers[k]:
                headers[k]['scale'] = 'GnBu'
            
            if 'format' not in headers[k]:
                headers[k]['format'] = '{:.1f}'
            
            setdmax = False
            setdmin = False
            try:
                headers[k]['dmax'] = float(headers[k]['max'])
            except KeyError:
                headers[k]['dmax'] = float("-inf")
                setdmax = True
            
            try:
                headers[k]['dmin'] = float(headers[k]['min'])
            except KeyError:
                headers[k]['dmin'] = float("inf")
                setdmin = True
            
            # Figure out the min / max if not supplied
            if setdmax or setdmin:
                for (sname, samp) in data.items():
                    try:
                        val = float(samp[k])
                        if 'modify' in headers[k] and callable(headers[k]['modify']):
                            val = float(headers[k]['modify'](val))
                        if setdmax:
                            headers[k]['dmax'] = max(headers[k]['dmax'], val)
                        if setdmin:
                            headers[k]['dmin'] = min(headers[k]['dmin'], val)
                    except KeyError:
                        pass # missing data - skip
        
            report.general_stats[namespace] = {
                'data': data,
                'headers': headers
            }
        
        return None
    
    def add_data_source(self, f=None, s_name=None, source=None, module=None, section=None):
        try:
            if module is None:
                module = self.name
            if section is None:
                section = 'all_sections'
            if s_name is None:
                s_name = f['s_name']
            if source is None:
                source = os.path.abspath(os.path.join(f['root'], f['fn']))
            report.data_sources[module][section][s_name] = source
        except AttributeError:
            logger.warning('Tried to add data source for {}, but was missing fields data'.format(self.name))
        
        
    
    def plot_xy_data(self, data, pconfig={}):
        """ Plot a line graph with X,Y data. See CONTRIBUTING.md for
        further instructions on use.
        :param data: 2D dict, first keys as sample names, then x:y data pairs
        :param pconfig: optional dict with config key:value pairs. See CONTRIBUTING.md
        :return: HTML and JS, ready to be inserted into the page
        """
        
        # Given one dataset - turn it into a list
        if type(data) is not list:
            data = [data]
        
        # Generate the data dict structure expected by HighCharts series
        plotdata = list()
        for d in data:
            thisplotdata = list()
            for s in sorted(d.keys()):
                pairs = list()
                maxval = 0
                if 'categories' in pconfig:
                    pconfig['categories'] = list()
                    for k in d[s].keys():
                        pconfig['categories'].append(k)
                        pairs.append(d[s][k])
                        maxval = max(maxval, d[s][k])
                else:
                    for k in sorted(d[s].keys()):
                        pairs.append([k, d[s][k]])
                        maxval = max(maxval, d[s][k])
                if maxval > 0 or pconfig.get('hide_empty') is not True:
                    this_series = { 'name': s, 'data': pairs }
                    try:
                        this_series['color'] = pconfig['colors'][s]
                    except: pass
                    thisplotdata.append(this_series)
            plotdata.append(thisplotdata)
        
        # Add on annotation data series
        try:
            for s in pconfig['extra_series']:
                plotdata[0].append(s)
        except KeyError:
            pass
        
        # Build the HTML for the page
        if pconfig.get('id') is None:
            pconfig['id'] = 'mqc_hcplot_'+''.join(random.sample(letters, 10))
        html = '<div class="mqc_hcplot_plotgroup">'
        
        # Buttons to cycle through different datasets
        if len(plotdata) > 1:
            html += '<div class="btn-group switch_group">\n'
            for k, p in enumerate(plotdata):
                active = 'active' if k == 0 else ''
                try: name = pconfig['data_labels'][k]['name']
                except: name = k+1
                try: ylab = 'data-ylab="{}"'.format(pconfig['data_labels'][k]['ylab'])
                except: ylab = 'data-ylab="{}"'.format(name) if name != k+1 else ''
                try: ymax = 'data-ymax="{}"'.format(pconfig['data_labels'][k]['ymax'])
                except: ymax = 'data-ymax="{}"'.format(name) if name != k+1 else ''
                html += '<button class="btn btn-default btn-sm {a}" data-action="set_data" {y} {ym} data-newdata="{k}" data-target="{id}">{n}</button>\n'.format(a=active, id=pconfig['id'], n=name, y=ylab, ym=ymax, k=k)
            html += '</div>\n\n'
        
        # The plot div
        html += '<div class="hc-plot-wrapper"><div id="{id}" class="hc-plot not_rendered hc-line-plot"><small>loading..</small></div></div></div> \n'.format(id=pconfig['id'])
        
        # Javascript with data dump
        html += '<script type="text/javascript"> \n\
            mqc_plots["{id}"] = {{ \n\
                "plot_type": "xy_line", \n\
                "datasets": {d}, \n\
                "config": {c} \n\
            }} \n\
        </script>'.format(id=pconfig['id'], d=json.dumps(plotdata), c=json.dumps(pconfig));
        return html
    
    
    def plot_bargraph (self, data, cats=None, pconfig={}):
        """ Plot a horizontal bar graph. Expects a 2D dict of sample
        data. Also can take info about categories. There are quite a
        few variants of how to use this function, see the docs for details.
        :param data: 2D dict, first keys as sample names, then x:y data pairs
                     Can supply a list of dicts and will have buttons to switch
        :param cats: optional list, dict or OrderedDict with plot categories
        :param pconfig: optional dict with config key:value pairs
        :return: HTML and JS, ready to be inserted into the page
        """
        
        # Given one dataset - turn it into a list
        if type(data) is not list:
            data = [data]
        
        # Check we have a list of cats
        try:
            cats[0].keys()
        except (KeyError, AttributeError):
            cats = [cats]
        
        # Check that we have cats at all - find them from the data
        for idx, cat in enumerate(cats):
            if cats[idx] is None:
                cats[idx] = list(set(k for s in data[idx].keys() for k in data[idx][s].keys() ))
        
        # Given a list of cats - turn it into a dict
        for idx, cat in enumerate(cats):
            if type(cat) is list:
                newcats = OrderedDict()
                for c in cat:
                    newcats[c] = {'name': c}
                cats[idx] = newcats
        
        # Parse the data into a chart friendly format
        plotsamples = list()
        plotdata = list()
        for idx, d in enumerate(data):
            hc_samples = sorted(list(d.keys()))
            hc_data = list()
            for c in cats[idx].keys():
                thisdata = list()
                for s in hc_samples:
                    try:
                        thisdata.append(d[s][c])
                    except KeyError:
                        pass
                if len(thisdata) > 0 and max(thisdata) > 0:
                    thisdict = { 'name': cats[idx][c]['name'], 'data': thisdata }
                    if 'color' in cats[idx][c]:
                        thisdict['color'] = cats[idx][c]['color']
                    hc_data.append(thisdict)
            if len(hc_data) > 0:
                plotsamples.append(hc_samples)
                plotdata.append(hc_data)
        
        if len(plotdata) == 0:
            logger.warning('Tried to make bar plot, but had no data')
            return '<p class="text-danger">Error - was not able to plot data.</p>'
        
        # TODO: Swap this temporary switch with config / template options
        if len(plotsamples[0]) > 50:
            return self.matplotlib_bargraph(plotdata, plotsamples, pconfig)
        else:
            return self.highcharts_bargraph(plotdata, plotsamples, pconfig)
    
    
    
    def highcharts_bargraph (self, plotdata, plotsamples=None, pconfig={}):
        """
        Build the HTML needed for a HighCharts bar graph. Should be
        called by plot_bargraph, which properly formats input data.
        """
        if pconfig.get('id') is None:
            pconfig['id'] = 'mqc_hcplot_'+''.join(random.sample(letters, 10))
        html = '<div class="mqc_hcplot_plotgroup">'
        
        # Counts / Percentages Switch
        if pconfig.get('cpswitch') is not False:
            if pconfig.get('cpswitch_c_active', True) is True:
                c_active = 'active'
                p_active = ''
            else:
                c_active = ''
                p_active = 'active'
                pconfig['stacking'] = 'percent'
            c_label = pconfig.get('cpswitch_counts_label', 'Counts')
            p_label = pconfig.get('cpswitch_percent_label', 'Percentages')
            html += '<div class="btn-group switch_group"> \n\
    			<button class="btn btn-default btn-sm {c_a}" data-action="set_numbers" data-target="{id}">{c_l}</button> \n\
    			<button class="btn btn-default btn-sm {p_a}" data-action="set_percent" data-target="{id}">{p_l}</button> \n\
    		</div> '.format(id=pconfig['id'], c_a=c_active, p_a=p_active, c_l=c_label, p_l=p_label)
            if len(plotdata) > 1:
                html += ' &nbsp; &nbsp; '
        
        # Buttons to cycle through different datasets
        if len(plotdata) > 1:
            html += '<div class="btn-group switch_group">\n'
            for k, p in enumerate(plotdata):
                active = 'active' if k == 0 else ''
                try: name = pconfig['data_labels'][k]
                except: name = k+1
                try: ylab = 'data-ylab="{}"'.format(pconfig['data_labels'][k]['ylab'])
                except: ylab = 'data-ylab="{}"'.format(name) if name != k+1 else ''
                html += '<button class="btn btn-default btn-sm {a}" data-action="set_data" {y} data-newdata="{k}" data-target="{id}">{n}</button>\n'.format(a=active, id=pconfig['id'], n=name, y=ylab, k=k)
            html += '</div>\n\n'
        
        # Plot and javascript function
        html += '<div class="hc-plot-wrapper"><div id="{id}" class="hc-plot not_rendered hc-bar-plot"><small>loading..</small></div></div> \n\
        </div> \n\
        <script type="text/javascript"> \n\
            mqc_plots["{id}"] = {{ \n\
                "plot_type": "bar_graph", \n\
                "samples": {s}, \n\
                "datasets": {d}, \n\
                "config": {c} \n\
            }} \n\
        </script>'.format(id=pconfig['id'], s=json.dumps(plotsamples), d=json.dumps(plotdata), c=json.dumps(pconfig));
        
        return html
    
    
    def matplotlib_bargraph (self, plotdata, plotsamples, pconfig={}):
        """
        Plot a bargraph with Matplot lib and return a HTML string with the base64
        encoded image within it. Should be called by plot_bargraph, which properly
        formats the input data.
        """
        if pconfig.get('id') is None:
            pconfig['id'] = 'mqc_mplplot_'+''.join(random.sample(letters, 10))
        
        # Set up figure
        plt_height = min(30, max(6, len(plotsamples[0]) / 2.3))
        fig = plt.figure(figsize=(14, plt_height), frameon=False)
        axes = fig.add_subplot(111)
        y_ind = range(len(plotsamples[0]))
        bar_width = 0.8
        # Same defaults as HighCharts for consistency
        default_colors = ['#7cb5ec', '#434348', '#90ed7d', '#f7a35c', '#8085e9', 
                          '#f15c80', '#e4d354', '#2b908f', '#f45b5b', '#91e8e1']
        
        # Plot bars
        dlabels = []
        for idx, d in enumerate(plotdata[0]):
            # Get offset for stacked bars
            if idx == 0:
                prevdata = [0] * len(plotsamples[0])
            else:
                for i, p in enumerate(prevdata):
                    prevdata[i] += plotdata[0][idx-1]['data'][i]
            # Default colour index
            cidx = idx
            while cidx > len(default_colors):
                cidx -= len(default_colors)
            # Save the name of this series
            dlabels.append(d['name'])
            # Add the series of bars to the plot
            axes.barh(
                y_ind, d['data'], bar_width, left=prevdata,
                color=d.get('color', default_colors[cidx]), align='center', linewidth=1, edgecolor='w'
            )
        
        # Tidy up axes
        axes.tick_params(labelsize=8, direction='out', left=False, right=False, top=False, bottom=False)
        axes.set_xlabel(pconfig.get('ylab', '')) # I know, I should fix the fact that the config is switched
        axes.set_ylabel(pconfig.get('xlab', ''))
        axes.set_yticks(y_ind) # Specify where to put the labels
        axes.set_yticklabels(plotsamples[0]) # Set y axis sample name labels
        axes.set_ylim((-0.5, len(y_ind)-0.5)) # Reduce padding around plot area
        default_xlimits = axes.get_xlim()
        axes.set_xlim((pconfig.get('ymin', default_xlimits[0]),pconfig.get('ymax', default_xlimits[1])))
        if 'title' in pconfig:
            plt.text(0.5, 1.05, pconfig['title'], horizontalalignment='center', fontsize=16, transform=axes.transAxes)
        axes.grid(True, zorder=0, which='both', axis='x', linestyle='-', color='#dedede', linewidth=1)
        axes.set_axisbelow(True)
        axes.spines['right'].set_visible(False)
        axes.spines['top'].set_visible(False)
        axes.spines['bottom'].set_visible(False)
        axes.spines['left'].set_visible(False)
        
        # Legend
        lgd = axes.legend(dlabels, loc='lower center', bbox_to_anchor=(0, -0.22, 1, .102), ncol=5, mode='expand', fontsize=8, frameon=False)
        
        # Tight layout - makes sure that legend fits in and stuff
        plt.tight_layout(rect=[0,0.08,1,0.92])
        
        # Output the figure to a base64 encoded string
        img_buffer = io.BytesIO()
        fig.savefig(img_buffer, format='png', bbox_extra_artists=(lgd,), bbox_inches='tight')
        b64_img = base64.b64encode(img_buffer.getvalue()).decode('utf8')
        plt.close(fig)
        img_buffer.close()
        
        return '<div class="mqc_mplplot_plotgroup"><img src="data:image/png;base64,{}"/></div>'.format(b64_img)
        
    
    def write_data_file(self, data, fn, sort_cols=False, data_format=None):
        """ Redirects to report.write_data_file() """
        report.write_data_file(data, fn, sort_cols, data_format)
