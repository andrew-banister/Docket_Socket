#########################################################################    
# 
# Program name:    views.py (Docket Socket)
# Programmer:      Andrew Banister, Paul Mack, Rob Letzler
# programmed:      July - September, 2016
#
# Purpose: Establishes a GAO intranet facing website that downloads 
# all  public comments, primary documents, and/or supporting documents 
# from regulations.gov for a specific docket number
# 
##########################################################################

#import Docket Socket functions
import os
import re
import time
import subprocess
import requests
import shutil
import operator
import glob
import xlsxwriter
from datetime import datetime
#import Django functions
from django.shortcuts import render
from django import forms
from django.contrib import messages
from django.core.mail import send_mail

class DocketForm(forms.Form):
    DOC_TYPES = (
    ("comments", "Comments"),
    ("primary", "Primary Documents"),
    ("supporting", "Supporting Documents")
    )
    docket_number = forms.CharField(label='Docket Number', max_length=100)
    email = forms.EmailField()
    doc_type = forms.MultipleChoiceField(choices=DOC_TYPES, widget=forms.CheckboxSelectMultiple)

def isdocket(docket_ID):
    """Check to see if records exist for the docket number.
    
    Arg:
            docket_ID: The docket number requested.
    Returns:
            True if records exist for the given docket number
            The request object for the docket number
    """
    request_response = check_quota_and_get("http://api.data.gov:80/regulations/v3/documents.json?api_key=06oqGOmSQFYA1K5d4cOQ3estOJ0TfokvaSERlwXq&countsOnly=0&dktid=%s&rpp=1000" % docket_ID)
    number_of_records = request_response.json().get("totalNumRecords")

    return number_of_records > 0, request_response

def home(request):
    # if this is a POST request we need to process the form data
    if request.method == 'POST':
        # create a form instance and populate it with data from the request:
        form = DocketForm(request.POST)
        # check whether it's valid:
        if form.is_valid():
            docket_number = form.cleaned_data['docket_number']
            email = form.cleaned_data['email']
            doc_type = form.cleaned_data['doc_type']
            # do additional check that email ends in gao.gov
            if email[-7:].lower() != 'gao.gov':
                    messages.error(request, 'Email must be GAO email')
                    return render(request, 'html/error.html')
            # check if the docket number is valid
            docket_request = isdocket(docket_number)
            if docket_request[0]:
                # # BEGIN MAIN DOWNLOAD # #
                    docket_socket("/var/docket_process_files", docket_request[1], docket_number, doc_type, email)
                    return render(request, 'html/results.html', {'email':email,'docket':docket_number})
            else:
                messages.error(request, 'No Docket found for Docket Number: %s' % docket_number)
                return render(request, 'html/error.html')

        else:
            errors = form.errors.as_data()
            print(errors)
            for field in errors:
                messages.error(request, 'The field ' + field + ' does not have a valid value')
            return render(request, 'html/error.html')
    # if a GET (or any other method) we'll create a blank form
    else:
        form = DocketForm()

    return render(request, 'html/home.html', {'form': form})

def makefolders(directory, docket_no, primary_on, supporting_on, comments_on):
    """Makes folders for the docket number.
    
    Creates folders for the docket number based on type of documents requested.
    If only one type of document is requested, only one folder is created with 
    the folder name being the docket number. If multiple types are requested,
    a head directory (name = docket number_'selected types') is created with
    subdirectories named by the type of document requested.
    
    ex. 1: OCC-2013-0003_Primary.zip, OCC-2013-0003_Supporting, OCC-2013-0003_Comments.zip 
    2: OCC-2013-0003_Primary_Supporting.zip, OCC-2013-0003_Primary_Comments.zip, OCC-2013-0003_Supporting_Comments.zip 
    3: OCC-2013-0003.zip

    Arg:
            directory: The path the folders are created in.
            docket_no: The docket number requested.
            primary_on: True if Primary Documents requested.
            supporting_on: True if Supporting Documents requested.
            comments_on: True if Comments requested.
    Returns:
            A dictionary containing the path names for each of the document types.
    """
    comment_path = ""
    primary_path = ""
    supporting_path = ""
    if sum([comments_on,primary_on,supporting_on]) == 3:
        path = os.path.join(directory, docket_no)
    elif sum([comments_on,primary_on,supporting_on]) == 2:
        path = os.path.join(directory,docket_no + "_"+primary_on*"Primary_"+supporting_on*"Supporting"+(not primary_on)*"_"+comments_on*"Comments")
    elif sum([comments_on,primary_on,supporting_on]) == 1:
        path = os.path.join(directory,docket_no + "_"+primary_on*"Primary"+supporting_on*"Supporting"+comments_on*"Comments")
        if primary_on: primary_path = path
        if supporting_on: supporting_path = path
        if comments_on: comment_path = path
    else:
        raise ValueError('Unable to create folders docket type not selected')
    os.makedirs(path,exist_ok=True)

    if sum([comments_on,primary_on,supporting_on]) != 1:
        if primary_on:
            primary_path = os.path.join(path, "Primary_Documents")
            os.makedirs(primary_path)
        if supporting_on:
            supporting_path = os.path.join(path, "Supporting_Documents")
            os.makedirs(supporting_path,exist_ok=True)
        if comments_on:
            comment_path = os.path.join(path, "Comments")
            os.makedirs(comment_path,exist_ok=True)
   
    return {"Path":path, "Primary":primary_path, "Supporting": supporting_path, "Comments":comment_path}

def dtime(path=""):
    """Gets current time or date modified time.
    
    If no path is provided, returns current time. If path is provided, returns time the file was modified.
    
    Arg:
            path: The path of the file you want to get the date modified.
    Returns:
            Current time or date modified time in the format 'mm/dd/yyyy H:M:S AM/PM'.
    """
    if path=="":
        return datetime.now().strftime('%m/%d/%Y %I:%M:%S %p')
    else:
        return datetime.fromtimestamp(int(os.stat(path).st_mtime)).strftime('%m/%d/%Y %I:%M:%S %p')

def check_quota_and_get(url):
    """Downloads url. If at the rate limit, wait 10 minutes and retry.
        
    Arg:
            url: The url to be downloaded.
    Returns:
            request_response: request object of the url.
    """
    #print("checking rate limit\n")
    #print(url)
    rate_limit_remaining = 1
    while True:
        if rate_limit_remaining>=0:
            request_response = requests.get(url)
            rate_limit_remaining = int(request_response.headers['X-RateLimit-Remaining'])
            assert rate_limit_remaining>=0, "Negative rate limit; heading for infinite loop!"
            if rate_limit_remaining == 0:
                print ('Rate limited. Waiting 10 minutes to retry', end='')
                time.sleep(600)
            else:
                return request_response
        else :
            time.sleep(10)

def getvalue(json,key):
    """Check if json key exists. If it does return the 'value' otherwise return an empty string.
    Arg:
            json: The json data to pull from.
            key: The key from the json data you are interested in.
    Returns:
            The string in the 'value' key of the requested key.
    """
    #example illustrating that "value" is nested:
    #  "comment":{"label":"Comment","value":"See attached file(s)"},"
    try:
        json.get(key)
        return json.get(key)['value']
    except:
        return ""

def dlfiles(list_of_file_formats, logfile, PATH):
    """Download files (attachments) from a list of file formats.

    Downloads the files in the list and saves them to the PATH.
    The file name is the document ID given, the attachment number (numbers sequentially),
    and the file extension. Writes The file download times and file sizes to the logfile.

    Arg:
            list_of_file_formats: List of different files to be downloaded
            logfile: variable for logfile
            PATH: output path
    Returns:
            files: A list of the files download locations.
    """
    #ex: list_of_file_formats = ["https://api.data.gov/regulations/v3/download?documentId=OCC-2013-0003-0138&attachmentNumber=1&contentType=pdf"]
    files=[]
    for file_format in list_of_file_formats:
        # use the file format url that was extracted from the file link in the document's API response
        # ex: file_format = "https://api.data.gov/regulations/v3/download?documentId=OCC-2013-0003-0062&attachmentNumber=1&contentType=pdf"
        document_url_to_request = file_format+"&api_key=06oqGOmSQFYA1K5d4cOQ3estOJ0TfokvaSERlwXq"
        # with a binary output file opened
        # request the attachment link and write the contents to the binary output file.
        request = check_quota_and_get(document_url_to_request)
        attachment_data = request.content 
        if request.headers.get('Content-Disposition') == 'None':
            logfile.write("[%s] Filetype not found for %s" % (dtime(), file_format))
        else:
            try: # find the file extension using regular expressions from the header
                file_ext = re.split(('(\\.[^.]+)"$'),request.headers.get('Content-Disposition'))[1]
            except:
                logfile.write("Could not find file extension. Check: " + request.headers.get('Content-Disposition'))
            try: # extract the document ID from the file url
                document_ID = re.search("documentId=(.*?\d)&", file_format).group(1)
            except:
                logfile.write("Could not find document ID. Check: " + file_format)
            try: # find the given attachment number from the url
                file_num = "_" + re.search("attachmentNumber=([0-9]+)", file_format).group(1)
            except:
                file_num = ""
            try:
                file_name_and_path = os.path.join(PATH, document_ID + file_num + file_ext)
                with open(file_name_and_path, "wb+") as attachment_output_file:
                    attachment_output_file.write(attachment_data)
                logfile.write("[%s] %s bytes\tDownloaded %s%s%s\n" % (dtime(file_name_and_path), os.stat(file_name_and_path).st_size, document_ID, file_num, file_ext))
                files.append(PATH + "/" + document_ID + file_num + file_ext)
            except:
                logfile.write("Could not download: " + file_format)
                files.append("N/A")
    return files

def dlcontent(document_ID, request_response, logfile, PATH):
    """Downloads all primary and supporting documents (including attachments).

    Uses the JSON data from the document ID . If the data is not restricted (usually because it is a duplicate),
    Downloads all of the file formats. Downloads the 'abstract' field if available. Also, downloads
    any attachments. Writes The file download times and file sizes to the logfile.

    Arg:
            document_ID: the document ID to be downloaded
            request_response: JSON data for the particular document ID
            logfile: variable for logfile
            PATH: output path
    Returns:
            file_links: the file locations of the downloaded documents
            attachment_links the file locations of the downloaded attachments
    """
    if getvalue(request_response.json(),'restrictReason')=="":
        try:
            list_of_file_formats = request_response.json()["fileFormats"]
            file_links=dlfiles(list_of_file_formats, logfile, PATH)[0]
        except:
            logfile.write("%s not downloaded" % document_ID)
            file_links=["N/A"]
    if getvalue(request_response.json(),"abstract")!="":    
        file_name_and_path = os.path.join(PATH,document_ID + "_abstract.html")
        with open(file_name_and_path, "w") as html_output_file:
            html_output_file.write(getvalue(request_response.json(),"abstract"))
        logfile.write("[%s] %s bytes\tDownloaded %_abstract.html\n" % (dtime(file_name_and_path), os.stat(file_name_and_path).st_size, document_ID))

    attachment_count = getvalue(request_response.json(),"attachmentCount")
    attachment_links=[]
    if attachment_count not in {0, '0', ''}:
        list_of_attachments = request_response.json()["attachments"]
        for attachment in list_of_attachments:
            list_of_file_formats = attachment["fileFormats"]
            attachment_links.extend(dlfiles(list_of_file_formats, logfile, PATH))
    return {"Link":file_links, "Attachments":attachment_links}
    
def dlcomments(document_ID, request_response, all_html_comments, logfile, PATH):
    """Downloads a single comments (including attachments).

    Uses the meta data to save as header and in the directory.
    Downloads the comment in html and any attachments. 
    Writes The file download times and file sizes to the logfile.

    Arg:
            document_ID: the document ID of the particular comment
            request_response: JSON data for the particular document ID
            all_html_comments: html file containing all html comments concatenated
            logfile: variable for logfile
            PATH: output path
    Returns:
            all_html_comments: adds the current comment and returns the html file containing all html comments concatenated
            file_link: the file location of the downloaded document
            attachment_links: file locations of the downloaded attachments.
    """
    title = getvalue(request_response.json(),"title")
    submitter_name = getvalue(request_response.json(),"submitterName")
    organization_name = getvalue(request_response.json(),"organization")
    attachment_count = getvalue(request_response.json(),"attachmentCount")
    comment_text = getvalue(request_response.json(),"comment")
    # attach meta data as header to html comment
    comment_all = "<h2>%s</h2><h3>%s</h3><b>Submitter Name:</b> %s <b>Organization Name:</b> %s<br><b>Comment: </b>%s" %(document_ID, title, submitter_name, organization_name, comment_text)            

    if comment_text.lower().strip() not in {"", "see attached", "see attached file", "see attached files", "see attached file(s)"}: #if contains attach
        all_html_comments = all_html_comments + "\n" + comment_all
        file_name_and_path = os.path.join(PATH, document_ID + ".html")
        file_link =  PATH + "/" + document_ID + ".html"      
        with open(file_name_and_path, "w") as html_output_file:
            html_output_file.write(comment_all)
        logfile.write("[%s] %s bytes\tDownloaded %s.html\n" % (dtime(file_name_and_path), os.stat(file_name_and_path).st_size, document_ID))
    else:
        file_link= "See attached"
    #download all attachments
    attachment_links=[]
    if attachment_count not in {0, '0', ''}:
            list_of_attachments = request_response.json()["attachments"]
            for attachment in list_of_attachments:
                list_of_file_formats = attachment["fileFormats"]
                attachment_links.extend(dlfiles(list_of_file_formats, logfile, PATH))
    return {"HTML":all_html_comments, "Link":file_link, "Attachments":attachment_links}
        
def getLinks(links, path):
    """Takes path locations and creates file links to be used in the xlsx directory.

    Uses the meta data to save as header and in the directory.
    Downloads the comment in html and any attachments. 
    Writes The file download times and file sizes to the logfile.

    Arg:
            links: the document ID of the particular comment
            path: JSON data for the particular document ID
    Returns:
            link: the file location of the downloaded document
            attachment: file locations of the downloaded attachments.
    """
    assert isinstance(links["Link"], str)
    link=""
    if links["Link"] != "See attached":
        link = links["Link"].replace(path,"")[1:]
    assert isinstance(links["Attachments"], list)
    attachment = [l.replace(path,"")[1:] for l in links["Attachments"]]
    return {"Link":link, "Attachments":attachment}

def docket_socket(directory, request_response, docket_ID, doctype, email):
    """Downloads all comments, primary, or supporting documents (including attachments).

    Downloads all records requested for a docket ID number. Saves all attachments.
    Writes The file download times and file sizes to the logfile.
    When downloading comments, saves an xlsx directory, and one html file containing all html comments.
    Every downloaded file is scanned with clamAV. If a virus is found, the file is quarantined and Rob Letzler is notified.
    The entire folder is compressed and copied to the www folder on the server website.
    Then an email is sent out to the user containing a file path to their requested zip folder.

    Arg:
            directory: Server file path used to save the documents
            request_response: Request object for the docket number
            docket_ID: Identification number of the docket to be downloaded (from Django form)
            doctype: Type of document to download (from Django form)
                -"Comments", Primary Documents", "Supporting Documents"
    Returns:
            Nothing. Downloads files.
    """
    try:
        # Make doctype into booleans
        primary_on = "primary" in doctype
        supporting_on = "supporting" in doctype
        comments_on = "comments" in doctype
        
        # Create Directories
        folder = makefolders(directory, docket_ID, primary_on, supporting_on, comments_on)
        PATH = folder['Path']
        # Start log file
        logfile = open(os.path.join(PATH,"docket_socket_log_file.log"),'w+')
        logfile.write("[%s] Began download of %s for %s\n" %(dtime(), ", ".join(doctype), docket_ID))

        #request_response = check_quota_and_get("http://api.data.gov:80/regulations/v3/documents.json?api_key=06oqGOmSQFYA1K5d4cOQ3estOJ0TfokvaSERlwXq&countsOnly=0&dktid=%s&rpp=1000" % docket_ID)

        #V2 could: keep a log in the Django database of the times certain requests were processed, so we could tell the user when their request will be processed
        number_of_records = request_response.json().get("totalNumRecords")
        logfile.write("[%s] Found %s records in the entire directory (includes, Primary, Supporting, and Comments)\n" % (dtime(), number_of_records))
        assert number_of_records > 0
        list_of_records = request_response.json()["documents"]
        # assign the list of records in the JSON response to a list and download
        # if over 1000 records, iterate through the pages creating a master list of all records
        # this is about the limit on the results per page that can be returned per request, NOT the hourly limit
        if number_of_records > 1000:
            record_count = 1000
            while record_count < number_of_records: # will continue downloading until all record meta data has been concatenated into one directory
                request_response = check_quota_and_get("http://api.data.gov:80/regulations/v3/documents.json?api_key=06oqGOmSQFYA1K5d4cOQ3estOJ0TfokvaSERlwXq&countsOnly=0&dktid=%s&rpp=1000&po=%s" % (docket_ID,record_count))
                # concatenates into one large directory
                list_of_records = list_of_records + request_response.json()["documents"]
                record_count = len(list_of_records)
        # Check all records were captured in our directory list of records
        assert number_of_records == len(list_of_records)
        # Sort list by documentID
        list_of_records=sorted(list_of_records, key=operator.itemgetter('documentId'))    
        
        # Create an Excel directory of records
        fields = ('Document ID', 'Link','Document Type', 'Document Title', 
         'Submitter Name', 'Organization Name', 'Date Posted', 'Attachment Count', 'Attachment Link(s)')
        xls_directory = xlsxwriter.Workbook(os.path.join(PATH, docket_ID + "_directory.xlsx"))
        worksheet = xls_directory.add_worksheet(docket_ID + " Directory")
        worksheet.set_column('A:A', len(docket_ID)*1.4)    # Widen column A
        worksheet.set_column('B:J', 18)    # Widen columns
        date_format = xls_directory.add_format({'num_format': 'mm/dd/yyyy'})

        # Write header in bold.
        bold = xls_directory.add_format({'bold': True})
        worksheet.write_row(0, 0, fields, bold) #write header
        row = 1 #Directory starts on row 1 before Looping
        blueU = xls_directory.add_format({'underline': True, 'font_color': 'blue'})

        if comments_on:
            all_html_comments = ""

        any_docs_downloaded = False
        #for each element in the list of records
        for document_data in list_of_records:
            #Do not download withdrawn documents
            if document_data["documentStatus"]=="Withdrawn":
                continue
        #   use the document API to learn more about each document ID, like OCC-2013-0003-0062
        # ex http://api.data.gov:80/regulations/v3/document.json?api_key=06oqGOmSQFYA1K5d4cOQ3estOJ0TfokvaSERlwXq&documentId=OCC-2013-0003-0062
            document_ID = document_data["documentId"]
            document_Type = document_data["documentType"]
            request_response = check_quota_and_get("http://api.data.gov:80/regulations/v3/document.json?api_key=06oqGOmSQFYA1K5d4cOQ3estOJ0TfokvaSERlwXq&documentId=%s" % document_ID)

        #Get chosen documents
            #Primary Documents
            if primary_on and document_Type not in {"Supporting & Related Material","Public Submission"}:
                primary_links = dlcontent(document_ID, request_response, logfile, folder['Primary'])
                all_links = getLinks(primary_links, PATH)
                any_docs_downloaded = True
            #Supporting Documents
            elif supporting_on and document_Type=="Supporting & Related Material":
                supporting_links = dlcontent(document_ID, request_response, logfile, folder['Supporting'])
                all_links = getLinks(supporting_links, PATH)
                any_docs_downloaded = True
            #Comments
            elif comments_on and document_Type=="Public Submission":
                comment_links = dlcomments(document_ID, request_response, all_html_comments, logfile, folder['Comments'])
                all_html_comments = comment_links["HTML"]
                all_links = getLinks(comment_links, PATH)
                any_docs_downloaded = True
            # Saved document to directory   
            if ((primary_on and document_Type not in {"Supporting & Related Material","Public Submission"}) or 
                (supporting_on and  document_Type=="Supporting & Related Material") or
                (comments_on and document_Type=="Public Submission")):
                title = getvalue(request_response.json(),"title")
                submitter_name = getvalue(request_response.json(),"submitterName")
                organization_name = getvalue(request_response.json(),"organization")
                try:
                    date_posted = request_response.json().get("postedDate")
                except:
                    date_posted = ""
                if date_posted != "":
                    reg = re.search("(.*?)T00", date_posted).group(1)
                    try:
                        date_posted = datetime.strptime(reg,'%Y-%m-%d')
                        worksheet.write_datetime(row,6,date_posted,date_format)
                    except:
                        worksheet.write(row,6,"")
                attachment_count = getvalue(request_response.json(),"attachmentCount")
                #save meta data to xls directory
                worksheet.write_row(row,0,(document_ID, '', document_Type, title,
                    submitter_name, organization_name))
                worksheet.write_number(row,7,int(attachment_count))
                #Write Link
                if all_links["Link"] == "See attached":
                    worksheet.write(row, 1, "See attached")
                else:
                    worksheet.write(row, 1, '=HYPERLINK("%s")' % all_links["Link"], blueU)
                #Write attachment links
                col = 8
                for attachment in all_links["Attachments"]:
                    worksheet.write(row, col, '=HYPERLINK("%s")' % attachment, blueU)
                    col += 1
                row+=1

#        if any_docs_downloaded == False:
#                    messages.error(request, 'The docket appears to contain none of the document type that you specified')
#                    return render(request, 'html/error.html')    
        if comments_on: #save all_html_comments
            file_name_and_path = os.path.join(PATH, docket_ID + "_all_comments.html")
            with open(file_name_and_path, "w") as html_output_file:
                html_output_file.write(all_html_comments)
            logfile.write("[%s] %s bytes\tDownloaded %s_all_comments.html\n" % (dtime(file_name_and_path), os.stat(file_name_and_path).st_size, document_ID))
        
        # Remove empty directories
        for s_target in os.listdir(PATH):
            s_path = os.path.join(PATH, s_target)
            if os.path.isdir(s_path) and not os.listdir(s_path):
                os.rmdir(s_path)
        xls_directory.close()
        logfile.close()

        # Run ClamAV virus scan on every file downloaded
        project_path=PATH+"/*"
        original_files = glob.glob(project_path)

        quarantine_path = os.path.normpath(os.path.join(PATH,"flagged_by_clam_AV/*"))
        os.makedirs(quarantine_path[:-2],exist_ok=True)
        print(os.path.join(project_path[:-2],"antivirus_scan.log"))
        subprocess.run(["clamscan", project_path[:-2], "--recursive", "--move="+quarantine_path[:-2], "--log="+os.path.join(project_path[:-2],"antivirus_scan.log")])
        post_scan_files = glob.glob(project_path)
        quarantine_files = glob.glob(quarantine_path)
        print(quarantine_files)
        if quarantine_files==[]:
            #number can grow if we add an antivirus log; or stay the same if the antivirus log already existed or could not be created.  that is not worrying
            assert len(post_scan_files)>=len(original_files), "The number of files dropped during the virus scan, but no viruses were quarantined.  Something's odd!"
        else:
#            print(str(['File(s) in your docket download flagged as potential viruses', 'clamAV flagged files in your docket download and moved them to ' + quarantine_path[:-2] + "\n Rob Letzler in ARM has been notified and will investigate. The following files were quarantined and not included in your ZIP file:  " +str(quarantine_files), 'letzlerr@gao.gov', [email, "letzlerr@gao.gov"]]))
            send_mail('File(s) in your docket download flagged as potential viruses', 'clamAV flagged files in your docket download and moved them to ' + quarantine_path + "\n Rob Letzler in ARM has been notified and will investigate. The following files were quarantined and not included in your ZIP file:  " +str(quarantine_files), 'letzlerr@gao.gov', [email, "letzlerr@gao.gov"], fail_silently=False)


        #zip all files downloaded, including log and directory
        shutil.make_archive(PATH, 'zip', PATH)
        #Copy zip file to www folder and send email
        ZIPPATH="/var/www/docket"
        shutil.copy(PATH+".zip",ZIPPATH)
        #os.remove(PATH) #Uncomment if we want to delete original folder
        print("/docket/"+os.path.split(PATH)[1]+".ZIP")
        send_mail('Your docket download is complete', 'Your docket download is complete and is available from [WEB ADDRESS TBD]/docket/' + os.path.split(PATH)[1] + '.ZIP', 'letzlerr@gao.gov', [email], fail_silently=False)
    except Exception as e:
        print("Failed to download data due to {}".format(e))
